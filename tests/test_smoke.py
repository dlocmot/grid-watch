import grid_watch


def test_package_exposes_version():
    assert isinstance(grid_watch.__version__, str)
    assert grid_watch.__version__
