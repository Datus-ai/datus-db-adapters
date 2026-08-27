-- Executed once, during TiDB's first bootstrap (--initialize-sql-file).
-- TiDB has no MYSQL_DATABASE/MYSQL_USER environment variables.
CREATE DATABASE IF NOT EXISTS test;
CREATE USER IF NOT EXISTS 'test_user'@'%' IDENTIFIED BY 'test_password';
GRANT ALL PRIVILEGES ON *.* TO 'test_user'@'%';
