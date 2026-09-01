# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for ClickZetta configuration classes."""

import pytest
from pydantic import ValidationError


class TestClickZettaConfig:
    """Test suite for ClickZetta configuration validation."""

    def test_valid_configuration(self):
        """Test valid configuration creation."""
        from datus_clickzetta.config import ClickZettaConfig

        # Test minimal required configuration
        config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
        )

        assert config.service == "test-service.clickzetta.com"
        assert config.username == "testuser"
        assert config.password == "testpass"
        assert config.instance == "test_instance"
        assert config.workspace == "test_workspace"
        assert config.schema_name == "PUBLIC"  # default value
        assert config.vcluster == "DEFAULT_AP"  # default value
        assert config.secure is None  # default value
        assert config.hints is None  # default value
        assert config.extra is None  # default value

    def test_configuration_with_all_options(self):
        """Test configuration with all optional parameters."""
        from datus_clickzetta.config import ClickZettaConfig

        config = ClickZettaConfig(
            service="prod-service.clickzetta.com",
            username="produser",
            password="prodpass",
            instance="prod_instance",
            workspace="prod_workspace",
            schema="CUSTOM_SCHEMA",
            vcluster="CUSTOM_VCLUSTER",
            secure=True,
            hints={"sdk.job.timeout": 600, "query_tag": "Custom Query"},
            extra={"custom_param": "custom_value"},
        )

        assert config.service == "prod-service.clickzetta.com"
        assert config.username == "produser"
        assert config.password == "prodpass"
        assert config.instance == "prod_instance"
        assert config.workspace == "prod_workspace"
        assert config.schema_name == "CUSTOM_SCHEMA"
        assert config.vcluster == "CUSTOM_VCLUSTER"
        assert config.secure is True
        assert config.hints == {"sdk.job.timeout": 600, "query_tag": "Custom Query"}
        assert config.extra == {"custom_param": "custom_value"}

    def test_configuration_validation_errors(self):
        """Test configuration validation with missing required fields."""
        from datus_clickzetta.config import ClickZettaConfig

        # Test missing service
        with pytest.raises(ValidationError) as exc_info:
            ClickZettaConfig(
                # service missing
                username="testuser",
                password="testpass",
                instance="test_instance",
                workspace="test_workspace",
            )
        assert "service" in str(exc_info.value)

        # Test missing username
        with pytest.raises(ValidationError) as exc_info:
            ClickZettaConfig(
                service="test-service.clickzetta.com",
                # username missing
                password="testpass",
                instance="test_instance",
                workspace="test_workspace",
            )
        assert "username" in str(exc_info.value)

        # Test missing password
        with pytest.raises(ValidationError) as exc_info:
            ClickZettaConfig(
                service="test-service.clickzetta.com",
                username="testuser",
                # password missing
                instance="test_instance",
                workspace="test_workspace",
            )
        assert "password" in str(exc_info.value)

        # Test missing instance
        with pytest.raises(ValidationError) as exc_info:
            ClickZettaConfig(
                service="test-service.clickzetta.com",
                username="testuser",
                password="testpass",
                # instance missing
                workspace="test_workspace",
            )
        assert "instance" in str(exc_info.value)

        # Test missing workspace
        with pytest.raises(ValidationError) as exc_info:
            ClickZettaConfig(
                service="test-service.clickzetta.com",
                username="testuser",
                password="testpass",
                instance="test_instance",
                # workspace missing
            )
        assert "workspace" in str(exc_info.value)

    def test_configuration_field_types(self):
        """Test configuration field type validation."""
        from datus_clickzetta.config import ClickZettaConfig

        # Test boolean secure field
        config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
            secure=False,
        )
        assert config.secure is False

        # Test dictionary hints field
        config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
            hints={},
        )
        assert config.hints == {}

        # Test dictionary extra field
        config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
            extra={"key": "value"},
        )
        assert config.extra == {"key": "value"}

    def test_configuration_validation_empty_required_fields(self):
        """Test that empty required fields raise validation errors."""
        from datus_clickzetta.config import ClickZettaConfig

        # Test that empty required fields fail validation
        with pytest.raises(ValidationError):
            ClickZettaConfig(
                service="",  # Empty required field should fail
                username="testuser",
                password="testpass",
                instance="test_instance",
                workspace="test_workspace",
            )

    def test_configuration_defaults(self):
        """Test default values for optional fields."""
        from datus_clickzetta.config import ClickZettaConfig

        config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
        )

        # Test that defaults are correctly set
        assert config.schema_name == "PUBLIC"
        assert config.vcluster == "DEFAULT_AP"
        assert config.secure is None
        assert config.hints is None
        assert config.extra is None

    def test_configuration_serialization(self):
        """Test configuration serialization and deserialization."""
        from datus_clickzetta.config import ClickZettaConfig

        # Create configuration
        original_config = ClickZettaConfig(
            service="test-service.clickzetta.com",
            username="testuser",
            password="testpass",
            instance="test_instance",
            workspace="test_workspace",
            schema="CUSTOM_SCHEMA",
            secure=True,
            hints={"key": "value"},
        )

        # Serialize to dict using aliases
        config_dict = original_config.model_dump(by_alias=True)

        # Create new config from dict
        new_config = ClickZettaConfig(**config_dict)

        # Verify they are equivalent
        assert new_config.service == original_config.service
        assert new_config.username == original_config.username
        assert new_config.password == original_config.password
        assert new_config.instance == original_config.instance
        assert new_config.workspace == original_config.workspace
        assert new_config.schema_name == original_config.schema_name
        assert new_config.secure == original_config.secure
        assert new_config.hints == original_config.hints
