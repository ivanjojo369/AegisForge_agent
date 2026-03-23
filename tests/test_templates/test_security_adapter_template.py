from pathlib import Path


def test_security_adapter_template_files_exist():
    root = Path("templates/security_adapter")
    assert (root / "adapter_config.template.toml").exists()
    assert (root / "Dockerfile.template").exists()
    assert (root / "README.template.md").exists()
"""
This test verifies that the necessary template files for the security adapter exist in the expected location. It checks for the presence of the adapter configuration template, Dockerfile template, and README template. These files are essential for users who want to implement a security adapter based on the provided templates, ensuring they have the necessary resources to get started.
"""
