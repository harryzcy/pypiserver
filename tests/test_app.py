#! /usr/bin/env py.test

# Builtin imports
import os
import pathlib
import xmlrpc.client as xmlrpclib
from html import unescape

# Third party imports
import pytest
import webtest

# Local Imports
from tests.test_pkg_helpers import files, invalid_files
from pypiserver import __main__, bottle_wrapper, _app
from pypiserver.backend import CachingFileBackend

# Enable logging to detect any problems with it
##

__main__.init_logging()


@pytest.fixture
def app(tmpdir):
    from pypiserver import app

    return app(
        roots=[pathlib.Path(tmpdir.strpath)],
        authenticate=[],
        password_file=".",
        backend_arg="simple-dir",
    )


@pytest.fixture
def testapp(app):
    """Return a webtest TestApp initiated with pypiserver app"""
    bottle_wrapper.debug(True)
    return webtest.TestApp(app)


@pytest.fixture
def root(tmpdir):
    """Return a pytest temporary directory"""
    return tmpdir


@pytest.fixture
def priv(app):
    b = bottle_wrapper.Bottle()
    b.mount("/priv/", app)
    return b


@pytest.fixture
def testpriv(priv):
    return webtest.TestApp(priv)


@pytest.fixture
def search_xml():
    """Return an xml dom suitable for passing to search"""
    xml = "<xml><methodName>search</methodName><string>test</string></xml>"
    return xml


@pytest.fixture(
    params=[
        "  ",  # Mustcontain test below fails when string is empty.
        "Hey there!",
        "<html><body>Hey there!</body></html>",
    ]
)
def welcome_file_no_vars(request, root):
    """Welcome file fixture

    :param request: pytest builtin fixture
    :param root: root temporary directory
    """
    wfile = root.join("testwelcome.html")
    wfile.write(request.param)

    return wfile


@pytest.fixture()
def welcome_file_all_vars(request, root):
    msg = """
    {{URL}}
    {{VERSION}}
    {{NUMPKGS}}
    {{PACKAGES}}
    {{SIMPLE}}
    """
    wfile = root.join("testwelcome.html")
    wfile.write(msg)

    return wfile


@pytest.fixture
def add_file_to_root(app):
    def file_adder(root, filename, content=""):
        root.join(filename).write(content)
        # bottle has deprecated the use of attribute access
        # in configdicts
        try:
            backend = app.config.backend
            if isinstance(backend, CachingFileBackend):
                backend.cache_manager.invalidate_root_cache(root)
        except AttributeError:
            pass

    return file_adder


def test_root_count(root, testapp, add_file_to_root):
    """Test that the welcome page count updates with added packages

    :param root: root temporary directory fixture
    :param testapp: webtest TestApp
    """
    resp = testapp.get("/")
    resp.mustcontain("PyPI compatible package index serving 0 packages")
    add_file_to_root(root, "Twisted-11.0.0.tar.bz2")
    resp = testapp.get("/")
    resp.mustcontain("PyPI compatible package index serving 1 packages")


def test_root_hostname(testapp):
    resp = testapp.get("/", headers={"Host": "systemexit.de"})
    resp.mustcontain(
        "easy_install --index-url http://systemexit.de/simple/ PACKAGE"
    )
    # go("http://systemexit.de/")


def test_root_welcome_msg_no_vars(root, welcome_file_no_vars):
    from pypiserver import app

    app = app(root=root.strpath, welcome_file=welcome_file_no_vars.strpath)
    testapp = webtest.TestApp(app)
    resp = testapp.get("/")
    from pypiserver import __version__ as pver

    resp.mustcontain(welcome_file_no_vars.read(), no=pver)


def test_root_welcome_msg_all_vars(root, welcome_file_all_vars):
    from pypiserver import app

    app = app(root=root.strpath, welcome_file=welcome_file_all_vars.strpath)
    testapp = webtest.TestApp(app)
    resp = testapp.get("/")

    from pypiserver import __version__ as pver

    resp.mustcontain(pver)


def test_root_welcome_msg_antiXSS(testapp):
    """https://github.com/pypiserver/pypiserver/issues/77"""
    resp = testapp.get("/?<alert>Red</alert>", headers={"Host": "somehost.org"})
    resp.mustcontain("alert", "somehost.org", no="<alert>")


def test_root_remove_not_found_msg_antiXSS(testapp):
    """https://github.com/pypiserver/pypiserver/issues/77"""
    resp = testapp.post(
        "/",
        expect_errors=True,
        headers={"Host": "somehost.org"},
        params={
            ":action": "remove_pkg",
            "name": "<alert>Red</alert>",
            "version": "1.1.1",
        },
    )
    resp.mustcontain("alert", "somehost.org", no="<alert>")


def test_packages_redirect(testapp):
    resp = testapp.get("/packages")
    assert resp.status_code >= 300
    assert resp.status_code < 400
    assert resp.location.endswith("/packages/")


def test_packages_empty(testapp):
    resp = testapp.get("/packages/")
    assert len(resp.html("a")) == 0


def test_health_default_endpoint(testapp):
    resp = testapp.get("/health")
    assert resp.status_int == 200
    assert "Ok" in resp


def test_health_customized_endpoint(root):
    from pypiserver import app

    _app = app(root=root.strpath, health_endpoint="/healthz")
    testapp = webtest.TestApp(_app)
    resp = testapp.get("/healthz")
    assert resp.status_int == 200
    assert "Ok" in resp


def test_health_invalid_customized_endpoint(root):
    from pypiserver import app

    with pytest.raises(RuntimeError, match="overlaps with existing routes"):
        app(root=root.strpath, health_endpoint="/simple")


def test_favicon(testapp):
    testapp.get("/favicon.ico", status=404)


def test_fallback(testapp):
    assert not testapp.app._pypiserver_config.disable_fallback
    resp = testapp.get("/simple/pypiserver/", status=302)
    assert resp.headers["Location"] == "https://pypi.org/simple/pypiserver/"


def test_no_fallback(testapp):
    testapp.app._pypiserver_config.disable_fallback = True
    testapp.get("/simple/pypiserver/", status=404)


def test_serve_no_dotfiles(root, testapp):
    root.join(".foo-1.0.zip").write("secret")
    testapp.get("/packages/.foo-1.0.zip", status=404)


def test_packages_list_no_dotfiles(root, testapp):
    root.join(".foo-1.0.zip").write("secret")
    resp = testapp.get("/packages/")
    assert "foo" not in resp


def test_simple_redirect(testapp):
    resp = testapp.get("/simple")
    assert resp.status_code >= 300
    assert resp.status_code < 400
    assert resp.location.endswith("/simple/")


def test_simple_list_no_dotfiles(root, testapp):
    root.join(".foo-1.0.zip").write("secret")
    resp = testapp.get("/simple/")
    assert "foo" not in resp


def test_simple_list_no_dotfiles2(root, testapp):
    root.join(".foo-1.0.zip").write("secret")
    resp = testapp.get("/simple/")
    assert resp.html("a") == []


def test_serve_no_dotdir(root, testapp):
    root.mkdir(".subdir").join("foo-1.0.zip").write("secret")
    testapp.get("/packages/.subdir/foo-1.0.zip", status=404)


def test_packages_list_no_dotdir(root, testapp):
    root.mkdir(".subdir").join("foo-1.0.zip").write("secret")
    resp = testapp.get("/packages/")
    assert "foo" not in resp


def test_simple_list_no_dotdir(root, testapp):
    root.mkdir(".subdir").join("foo-1.0.zip").write("secret")
    resp = testapp.get("/simple/")
    assert "foo" not in resp


def test_simple_list_no_dotdir2(root, testapp):
    root.mkdir(".subdir").join("foo-1.0.zip").write("secret")
    resp = testapp.get("/simple/foo/")
    assert resp.html("a") == []


def test_simple_name_redirect(testapp):
    resp = testapp.get("/simple/foobar")
    assert resp.status_code >= 300
    assert resp.status_code < 400
    assert resp.location.endswith("/simple/foobar/")


@pytest.mark.parametrize(
    "package,normalized",
    [
        ("FooBar", "foobar"),
        ("Foo.Bar", "foo-bar"),
        ("foo_bar", "foo-bar"),
        ("Foo-Bar", "foo-bar"),
        ("foo--_.bar", "foo-bar"),
    ],
)
def test_simple_normalized_name_redirect(testapp, package, normalized):
    resp = testapp.get("/simple/{0}/".format(package))
    assert resp.status_code >= 300
    assert resp.status_code < 400
    assert resp.location.endswith("/simple/{0}/".format(normalized))


def test_simple_index(root, testapp):
    root.join("foobar-1.0.zip").write("")
    root.join("foobar-1.1.zip").write("")
    root.join("foobarbaz-1.1.zip").write("")
    root.join("foobar.baz-1.1.zip").write("")

    resp = testapp.get("/simple/foobar/")
    assert len(resp.html("a")) == 2


def test_simple_index_list(root, testapp):
    root.join("foobar-1.0.zip").write("")
    root.join("foobar-1.1.zip").write("")
    root.join("foobarbaz-1.1.zip").write("")
    root.join("foobar.baz-1.1.zip").write("")

    resp = testapp.get("/simple/")
    assert len(resp.html("a")) == 3


def test_simple_index_case(root, testapp):
    root.join("FooBar-1.0.zip").write("")
    root.join("FooBar-1.1.zip").write("")
    resp = testapp.get("/simple/foobar/")
    assert len(resp.html("a")) == 2


def test_nonroot_root(testpriv):
    resp = testpriv.get("/priv/", headers={"Host": "nonroot"})
    resp.mustcontain(
        "easy_install --index-url http://nonroot/priv/simple/ PACKAGE"
    )


def test_nonroot_root_with_x_forwarded_host(testapp):
    resp = testapp.get("/", headers={"X-Forwarded-Host": "forward.ed/priv/"})
    resp.mustcontain(
        "easy_install --index-url http://forward.ed/priv/simple/ PACKAGE"
    )
    resp.mustcontain("""<a href="/priv/packages/">here</a>""")


def test_nonroot_root_with_x_forwarded_host_without_trailing_slash(testapp):
    resp = testapp.get("/", headers={"X-Forwarded-Host": "forward.ed/priv"})
    resp.mustcontain(
        "easy_install --index-url http://forward.ed/priv/simple/ PACKAGE"
    )
    resp.mustcontain("""<a href="/priv/packages/">here</a>""")


def test_nonroot_simple_index(root, testpriv, add_file_to_root):
    add_file_to_root(root, "foobar-1.0.zip", "123")
    resp = testpriv.get("/priv/simple/foobar/")
    links = resp.html("a")
    assert len(links) == 1
    assert links[0]["href"].startswith("/priv/packages/foobar-1.0.zip#")


def test_nonroot_simple_index_with_x_forwarded_host(
    root, testapp, add_file_to_root
):
    add_file_to_root(root, "foobar-1.0.zip", "123")

    resp = testapp.get(
        "/simple/foobar/", headers={"X-Forwarded-Host": "forwarded.ed/priv/"}
    )
    links = resp.html("a")
    assert len(links) == 1
    assert links[0]["href"].startswith("/priv/packages/foobar-1.0.zip#")


def test_nonroot_simple_packages(root, testpriv, add_file_to_root):
    add_file_to_root(root, "foobar-1.0.zip", "123")
    resp = testpriv.get("/priv/packages/")
    links = resp.html("a")
    assert len(links) == 1
    assert "/priv/packages/foobar-1.0.zip#" in links[0]["href"]


def test_nonroot_simple_packages_with_x_forwarded_host(
    root, testapp, add_file_to_root
):
    add_file_to_root(root, "foobar-1.0.zip", "123")

    resp = testapp.get(
        "/packages/", headers={"X-Forwarded-Host": "forwarded/priv/"}
    )
    links = resp.html("a")
    assert len(links) == 1
    assert "/priv/packages/foobar-1.0.zip#" in links[0]["href"]


def test_root_no_relative_paths(testpriv):
    """https://github.com/pypiserver/pypiserver/issues/25"""
    resp = testpriv.get("/priv/")
    hrefs = [x["href"] for x in resp.html("a")]
    assert hrefs == [
        "/priv/packages/",
        "/priv/simple/",
        "https://pypi.org/project/pypiserver/",
    ]


def test_simple_index_list_no_duplicates(root, testapp):
    root.join("foo-bar-1.0.tar.gz").write("")
    root.join("foo_bar-1.0-py2.7.egg").write("")

    resp = testapp.get("/simple/")
    assert len(resp.html("a")) == 1


def test_simple_index_list_name_with_underscore(root, testapp):
    root.join("foo_bar-1.0.tar.gz").write("")
    root.join("foo_bar-1.0-py2.7.egg").write("")

    resp = testapp.get("/simple/")
    assert len(resp.html("a")) == 1
    hrefs = [x["href"] for x in resp.html("a")]
    assert hrefs == ["foo-bar/"]


def test_simple_index_egg_and_tarball(root, testapp):
    root.join("foo-bar-1.0.tar.gz").write("")
    root.join("foo_bar-1.0-py2.7.egg").write("")

    resp = testapp.get("/simple/foo-bar/")
    assert len(resp.html("a")) == 2


def test_simple_index_list_name_with_underscore_no_egg(root, testapp):
    root.join("foo_bar-1.0.tar.gz").write("")
    root.join("foo-bar-1.1.tar.gz").write("")

    resp = testapp.get("/simple/")
    assert len(resp.html("a")) == 1
    hrefs = set([x["href"] for x in resp.html("a")])
    assert hrefs == {"foo-bar/"}


def test_json_info(root, testapp):
    root.join("foobar-1.0.zip").write("")
    root.join("foobar-1.1.zip").write("")
    root.join("foobar-1.1-linux.zip").write("")
    root.join("foobarX-1.1.zip").write("")

    resp = testapp.get("/foobar/json")
    assert "info" in resp.json
    assert "releases" in resp.json
    assert len(resp.json["info"]) == 1
    assert len(resp.json["releases"]) == 2
    assert len(resp.json["releases"]["1.0"]) == 1
    assert len(resp.json["releases"]["1.1"]) == 2


def test_json_info_package_not_existing(root, testapp):
    resp = testapp.get("/foobar/json", status=404)


@pytest.mark.parametrize(
    "package,normalized",
    [
        ("FooBar", "foobar"),
        ("Foo.Bar", "foo-bar"),
        ("foo_bar", "foo-bar"),
        ("Foo-Bar", "foo-bar"),
        ("foo--_.bar", "foo-bar"),
    ],
)
def test_json_info_normalized_name_redirect(testapp, package, normalized):
    resp = testapp.get("/{0}/json".format(package))
    assert resp.status_code >= 300
    assert resp.status_code < 400
    assert resp.location.endswith("/{0}/json".format(normalized))


def test_no_cache_control_set(root, testapp):
    assert not testapp.app._pypiserver_config.cache_control
    root.join("foo_bar-1.0.tar.gz").write("")
    resp = testapp.get("/packages/foo_bar-1.0.tar.gz")
    assert "Cache-Control" not in resp.headers


def test_cache_control_set(root):
    from pypiserver import app

    AGE = 86400
    app_with_cache = webtest.TestApp(app(root=root.strpath, cache_control=AGE))
    root.join("foo_bar-1.0.tar.gz").write("")
    resp = app_with_cache.get("/packages/foo_bar-1.0.tar.gz")
    assert "Cache-Control" in resp.headers
    assert resp.headers["Cache-Control"] == f"public, max-age={AGE}"


def test_upload_noAction(testapp):
    resp = testapp.post("/", expect_errors=1)
    assert resp.status == "400 Bad Request"
    assert "Missing ':action' field!" in unescape(resp.text)


def test_upload_badAction(testapp):
    resp = testapp.post("/", params={":action": "BAD"}, expect_errors=1)
    assert resp.status == "400 Bad Request"
    assert "Unsupported ':action' field: BAD" in unescape(resp.text)


@pytest.mark.parametrize(
    "package", [f[0] for f in files if f[1] and "/" not in f[0]]
)
def test_upload(package, root, testapp):
    resp = testapp.post(
        "/",
        params={":action": "file_upload"},
        upload_files=[("content", package, b"")],
    )
    assert resp.status_int == 200
    uploaded_pkgs = [f.basename for f in root.listdir()]
    assert len(uploaded_pkgs) == 1
    assert uploaded_pkgs[0].lower() == package.lower()


def test_upload_conflict_on_existing(root, testapp):
    package = "foo_bar-1.0.tar.gz"
    root.join("foo_bar-1.0.tar.gz").write("")

    resp = testapp.post(
        "/",
        params={":action": "file_upload"},
        upload_files=[("content", package, b"")],
        status=409,
    )

    assert resp.status_int == 409
    assert "Package 'foo_bar-1.0.tar.gz' already exists!" in unescape(resp.text)


@pytest.mark.parametrize(
    "package", [f[0] for f in files if f[1] and "/" not in f[0]]
)
def test_upload_with_signature(package, root, testapp):
    resp = testapp.post(
        "/",
        params={":action": "file_upload"},
        upload_files=[
            ("content", package, b""),
            ("gpg_signature", f"{package}.asc", b""),
        ],
    )
    assert resp.status_int == 200
    uploaded_pkgs = [f.basename.lower() for f in root.listdir()]
    assert len(uploaded_pkgs) == 2
    assert package.lower() in uploaded_pkgs
    assert f"{package.lower()}.asc" in uploaded_pkgs


@pytest.mark.parametrize("package", invalid_files)
def test_upload_badFilename(package, root, testapp):
    resp = testapp.post(
        "/",
        params={":action": "file_upload"},
        upload_files=[("content", package, b"")],
        expect_errors=1,
    )
    assert resp.status == "400 Bad Request"
    assert f"Bad filename: {package}" in resp.text


@pytest.mark.parametrize(
    "pkgs,matches",
    [
        ([], []),
        (["test-1.0.tar.gz"], [("test", "1.0")]),
        (
            ["test-1.0.tar.gz", "test-test-2.0.1.tar.gz"],
            [("test", "1.0"), ("test-test", "2.0.1")],
        ),
        (["test-1.0.tar.gz", "other-2.0.tar.gz"], [("test", "1.0")]),
        (["test-2.0-py2.py3-none-any.whl"], [("test", "2.0")]),
        (["other-2.0.tar.gz"], []),
    ],
)
def test_search(root, testapp, search_xml, pkgs, matches):
    """Test the search functionality at the RPC2 endpoint

    Calls the handle_rpc function by posting to the WebTest server with
    the string returned by the ``search_xml`` fixture as the request
    body. The result is parsed for convenience by the xmlrpc.client
    (xmlrpclib in Python 2.x). The parsed result is a 2-tuple. The
    second item is the method called, in this case always "search". The
    first item is a 1-tuple which contains a list of match information
    as dicts, e.g:

    ``(([{'version': '2.0', '_pypi_ordering': 0,
    'name': 'test', 'summary': '2.0'}],), 'search')``

    The pkgs parameter is a list of items to write to the packages
    directory, which should then be available for the subsequent
    search. The matches parameter is a list of 2-tuples of the form
    (``name``, ``version``), where ``name`` and ``version`` are the
    expected name and version matches for a search for the "test"
    package as specified by the search_xml fixture.

    :param root: root temporary directory fixture; used as packages dir
        for testapp
    :param testapp: webtest TestApp
    :param str search_xml: XML string roughly equivalent to a pip search
        for "test"
    :param pkgs: package file names to be written into packages
        directory
    :param matches: a list of 2-tuples containing expected (name,
        version) matches for the "test" query
    """
    for pkg in pkgs:
        root.join(pkg).write("")
    resp = testapp.post("/RPC2", search_xml)
    parsed = xmlrpclib.loads(resp.text)
    assert len(parsed) == 2 and parsed[1] == "search"
    if not matches:
        assert len(parsed[0]) == 1 and not parsed[0][0]
    else:
        assert len(parsed[0][0]) == len(matches) and parsed[0][0]
        for returned in parsed[0][0]:
            print(returned)
            assert returned["name"] in [match[0] for match in matches]
            assert returned["version"] in [match[1] for match in matches]


class TestRemovePkg:
    """The API allows removal of packages."""

    @pytest.mark.parametrize(
        "pkg, name, ver",
        (
            ("test-1.0.tar.gz", "test", "1.0"),
            ("test-1.0-py2-py3-none-any.whl", "test", "1.0"),
        ),
    )
    def test_remove_pkg(self, root, testapp, pkg, name, ver):
        """Packages can be removed via POST."""
        root.join(pkg).write("")
        assert len(os.listdir(str(root))) == 1
        params = {":action": "remove_pkg", "name": name, "version": ver}
        testapp.post("/", params=params)
        assert len(os.listdir(str(root))) == 0

    @pytest.mark.parametrize(
        "pkg, name, ver",
        (
            ("test-1.0.tar.gz", "test", "1.0"),
            ("test-1.0-py2-py3-none-any.whl", "test", "1.0"),
        ),
    )
    @pytest.mark.parametrize(
        "other",
        (
            "test-2.0.tar.gz",
            "test-2.0-py2-py3-none-any.whl",
            "other-1.0.tar.gz",
            "other-1.0-py2-py3-none-any.whl",
        ),
    )
    def test_remove_pkg_only_targeted(
        self, root, testapp, pkg, name, ver, other
    ):
        """Only the targeted package is removed."""
        root.join(pkg).write("")
        root.join(other).write("")
        assert len(os.listdir(str(root))) == 2
        params = {":action": "remove_pkg", "name": name, "version": ver}
        testapp.post("/", params=params)
        assert len(os.listdir(str(root))) == 1

    @pytest.mark.parametrize(
        "pkgs, name, ver",
        (
            (
                ("test-1.0.tar.gz", "test-1.0-py2-py3-none-any.whl"),
                "test",
                "1.0",
            ),
        ),
    )
    def test_all_instances_removed(self, root, testapp, pkgs, name, ver):
        """Test that all instances of the target are removed."""
        for pkg in pkgs:
            root.join(pkg).write("")
        assert len(os.listdir(str(root))) == len(pkgs)
        params = {":action": "remove_pkg", "name": name, "version": ver}
        testapp.post("/", params=params)
        assert len(os.listdir(str(root))) == 0

    @pytest.mark.parametrize(
        ("name", "version"),
        [
            (None, None),
            (None, ""),
            ("", None),
            (None, "1"),
            ("pkg", None),
            ("", "1"),
            ("pkg", ""),
        ],
    )
    def test_remove_pkg_missingNaveVersion(self, name, version, root, testapp):
        msg = f"Missing 'name'/'version' fields: name={name}, version={version}"
        params = {":action": "remove_pkg", "name": name, "version": version}
        params = dict((k, v) for k, v in params.items() if v is not None)
        resp = testapp.post("/", expect_errors=1, params=params)

        assert resp.status == "400 Bad Request"
        assert msg in unescape(resp.text)

    def test_remove_pkg_notFound(self, root, testapp):
        resp = testapp.post(
            "/",
            expect_errors=1,
            params={":action": "remove_pkg", "name": "foo", "version": "123"},
        )
        assert resp.status == "404 Not Found"
        assert "foo (123) not found" in unescape(resp.text)


def test_redirect_project_encodes_newlines():
    """Ensure raw newlines are url encoded in the generated redirect."""
    project = "\nSet-Cookie:malicious=1;"
    request = bottle_wrapper.Request(
        {"HTTP_X_FORWARDED_PROTO": "/\nSet-Cookie:malicious=1;"}
    )
    newpath = _app.get_bad_url_redirect_path(request, project)
    assert "\n" not in newpath
