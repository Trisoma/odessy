from conftest import discard_fixture_git


def test_disposable_git_cleanup_preserves_working_files_and_unrelated_repositories(tmp_path):
    root=tmp_path/'fixture';root.mkdir();(root/'.git').mkdir()
    (root/'.git/config').write_text('fixture')
    (root/'result.txt').write_text('keep evidence')
    child=tmp_path/'fixture-odessy-job';child.mkdir();(child/'.git').write_text('gitdir: fixture')
    unrelated=tmp_path/'other';unrelated.mkdir();(unrelated/'.git').mkdir()
    discard_fixture_git(root)
    assert not (root/'.git').exists() and not (child/'.git').exists()
    assert (root/'result.txt').read_text()=='keep evidence'
    assert (unrelated/'.git').is_dir()
    # The preservation assertion is complete; this fixture itself must not leave
    # another discoverable repository in the editor.
    discard_fixture_git(unrelated)
