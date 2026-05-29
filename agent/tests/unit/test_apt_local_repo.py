"""bundle/apt is shipped as a real local apt repository.

setup.sh stage 3 and scripts/deploy.sh must:
- register the bundle's apt/ as a file:// apt source (not call dpkg -i,
  not `apt install ./*.deb`),
- run a proper `apt-get install -y apptainer ...`,
- and tear the temporary source down afterwards.

build-sif-bundle.sh must generate Packages.gz (the apt repo index) so
the target can `apt-get update` against the local source. Operators
can then use familiar `apt list --installed | grep apptainer`,
`apt remove apptainer`, etc.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"
DEPLOY = REPO / "scripts" / "deploy.sh"
BUILD = REPO / "scripts" / "build-sif-bundle.sh"


def _read(p: Path) -> str:
    return p.read_text()


def test_setup_registers_file_source_and_uses_named_install():
    body = _read(SETUP)
    assert "loopcoder-local.list" in body, "setup.sh must use a named temp sources file"
    assert "deb [trusted=yes] file://" in body, \
        "setup.sh must register the bundle apt/ as a file:// apt source"
    # apt-get install of a $pkgs array (named packages, not paths).
    assert "apt-get install -y --no-install-recommends ${pkgs[*]}" in body, \
        "setup.sh must install the package list via standard apt-get"


def test_setup_does_not_install_debs_by_path():
    body = _read(SETUP)
    # The old pattern `apt-get install -y --no-install-recommends ./*.deb` /
    # `${debs[*]}` is gone; only named packages remain.
    assert "${debs[*]}" not in body
    assert "apt-get install -y --no-install-recommends ./*.deb" not in body


def test_setup_cleans_up_temp_sources_after_install():
    body = _read(SETUP)
    assert "rm -f '$list_file'" in body or "rm -f /etc/apt/sources.list.d/loopcoder-local.list" in body


def test_setup_regenerates_packages_gz_if_missing():
    body = _read(SETUP)
    assert "Packages.gz" in body
    assert "dpkg-scanpackages" in body


def test_build_sif_bundle_writes_packages_gz_on_24_04():
    body = _read(BUILD)
    assert "dpkg-scanpackages" in body
    assert "Packages.gz" in body
    # The non-24.04 guide-path command must also produce Packages.gz.
    assert "Packages.gz" in body.split("ONCE from a 24.04 host", 1)[-1]


def test_deploy_sh_uses_local_repo_pattern_not_pathwise_debs():
    # Inspect only executable lines; the comment may legitimately
    # mention the superseded ./*.deb pattern.
    code = "\n".join(
        ln for ln in _read(DEPLOY).splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    )
    assert "loopcoder-local.list" in code
    assert "deb [trusted=yes] file://" in code
    # Old path-wise install pattern is gone from executable code.
    assert "./*.deb" not in code
    assert "apt-get install -y --no-install-recommends" in code
    install_chunk = code.split("apt-get install -y --no-install-recommends", 1)[1][:200]
    assert "apptainer" in install_chunk


def test_install_package_list_is_consistent_between_setup_and_deploy():
    # Both scripts ask apt for the same top-level set so operators see
    # the same result either way.
    pkgs = ["apptainer", "python3.12", "python3.12-venv", "python3.12-dev",
            "python3-pip", "rsync", "curl", "ca-certificates", "jq", "tmux", "git"]
    s, d = _read(SETUP), _read(DEPLOY)
    for p in pkgs:
        assert p in s, f"setup.sh missing {p}"
        assert p in d, f"deploy.sh missing {p}"
