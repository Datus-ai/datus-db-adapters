use bson::{doc, Document};
use mongosql::{
    build_catalog_from_catalog_schema, catalog::Catalog, json_schema::Schema,
    options::ExcludeNamespacesOption, options::SqlOptions, SchemaCheckingMode,
};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};
use std::collections::BTreeMap;

#[pyclass(frozen)]
struct MongoSqlCatalog {
    catalog: Catalog,
    database: String,
}

#[pymethods]
impl MongoSqlCatalog {
    #[new]
    fn new(database: String, schema: &str) -> PyResult<Self> {
        let schemas: BTreeMap<String, BTreeMap<String, Schema>> = serde_json::from_str(schema)
            .map_err(|_| PyValueError::new_err("Invalid MongoDB schema_file"))?;
        if !schemas.contains_key(&database) {
            return Err(PyValueError::new_err(
                "MongoDB schema_file does not describe the configured database",
            ));
        }
        let catalog = build_catalog_from_catalog_schema(schemas)
            .map_err(|_| PyValueError::new_err("Invalid MongoDB collection schema"))?;
        Ok(Self { catalog, database })
    }

    fn translate<'py>(
        &self,
        py: Python<'py>,
        sql: &str,
        collection: &str,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let data = py.detach(|| {
            let translated = mongosql::translate_sql(
                &self.database,
                sql,
                &self.catalog,
                SqlOptions {
                    exclude_namespaces: ExcludeNamespacesOption::ExcludeNamespaces,
                    schema_checking_mode: SchemaCheckingMode::Strict,
                    allow_order_by_missing_columns: true,
                },
            )
            .map_err(|error| {
                PyValueError::new_err(format!("MongoSQL translation failed: {error}"))
            })?;
            if translated.target_db != self.database
                || translated.target_collection.as_deref() != Some(collection)
            {
                return Err(PyValueError::new_err(
                    "MongoSQL SELECT must read the requested collection in the configured database",
                ));
            }
            let properties = translated
                .result_set_schema
                .properties
                .ok_or_else(|| PyValueError::new_err("MongoSQL result schema has no fields"))?;
            let columns = translated
                .select_order
                .iter()
                .map(|path| {
                    let [name] = path.as_slice() else {
                        return Err(PyValueError::new_err("MongoSQL result field is not scalar"));
                    };
                    let schema = properties.get(name).ok_or_else(|| {
                        PyValueError::new_err("MongoSQL result field has no schema")
                    })?;
                    let schema = schema
                        .to_bson()
                        .map_err(|_| PyValueError::new_err("Invalid MongoSQL result schema"))?;
                    Ok(doc! { "name": name, "schema": schema })
                })
                .collect::<PyResult<Vec<Document>>>()?;
            bson::to_vec(&doc! { "pipeline": translated.pipeline, "columns": columns })
                .map_err(|_| PyValueError::new_err("Could not encode the MongoSQL translation"))
        })?;
        Ok(PyBytes::new(py, &data))
    }
}

#[pymodule]
fn _mongosql(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<MongoSqlCatalog>()
}
