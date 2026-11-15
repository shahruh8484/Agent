import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir(tmp_path_factory):
    """Keep the accounts database used by the API tests out of the repository."""
    os.environ["FC_DATA_DIR"] = str(tmp_path_factory.mktemp("fcdata"))
    yield
