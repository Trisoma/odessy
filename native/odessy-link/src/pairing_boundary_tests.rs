use std::io::Cursor;

#[test]
fn capabilities_report_experimental_pairing_without_echoing_codes() {
    let mut output = Vec::new();
    let input = b"{\"id\":\"request-1\",\"command\":\"join\",\"code\":\"012345\"}\n";
    crate::pairing::serve(Cursor::new(input), &mut output).unwrap();
    let text = String::from_utf8(output).unwrap();
    assert!(!text.contains("012345"));
    let reply: serde_json::Value = serde_json::from_str(text.trim()).unwrap();
    assert_eq!(reply["supported"], true);
    assert_eq!(reply["reason"], "experimental");
    assert_eq!(reply["state"], "closed");
}

#[test]
fn partial_or_oversized_messages_are_rejected_before_dispatch() {
    for input in [vec![b'x'; 65537], b"{\"command\":\"begin\"}".to_vec()] {
        let mut output = Vec::new();
        assert!(crate::pairing::serve(Cursor::new(input), &mut output).is_err());
        assert!(output.is_empty());
    }
}

#[test]
fn code_in_an_unsupported_or_malformed_request_is_not_rendered() {
    let mut output = Vec::new();
    crate::pairing::serve(Cursor::new(b"{\"id\":\"request-2\",\"command\":\"grant\",\"code\":\"654321\"}\n"), &mut output).unwrap();
    let text = String::from_utf8(output).unwrap();
    assert!(!text.contains("654321"));
    assert!(!text.contains("ready"));
}

#[test]
fn fresh_tls_configuration_tracks_enrollment_and_revocation() {
    let root = std::env::temp_dir().join(format!("odessy-pair-reload-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
    let path=root.join("trust.sqlite3");
    let store=crate::trust::Registry::open(&path).unwrap();
    let mine=crate::identity::Identity::generate("reloadlocal").unwrap();
    let peer=crate::identity::Identity::generate("reloadpeer").unwrap();
    assert!(crate::commands::fresh_tls_config(&mine,&path,false).is_err());
    store.add_peer("peer",&serde_json::to_string(&peer.bundle()).unwrap(),&peer.fingerprint(),None).unwrap();
    assert!(crate::commands::fresh_tls_config(&mine,&path,false).is_ok());
    store.revoke("peer").unwrap();
    assert!(crate::commands::fresh_tls_config(&mine,&path,false).is_err());
    drop(store);
    std::fs::remove_dir_all(&root).unwrap();
}

#[test]
fn revoked_records_never_count_as_execution_grants() {
    let root=std::env::temp_dir().join(format!("odessy-revocation-{}",crate::identity::random_installation_id().unwrap()));
    let store=crate::trust::Registry::open(&root.join("trust.sqlite3")).unwrap();
    let identity=crate::identity::Identity::generate("revokedpeer").unwrap();
    let mut peer=store.add_peer("peer",&serde_json::to_string(&identity.bundle()).unwrap(),&identity.fingerprint(),None).unwrap();
    peer.capability_mcp=true;peer.workspace=Some(root.to_string_lossy().to_string());peer.revoked=true;
    assert!(!peer.has_execution_grant());
    drop(store);std::fs::remove_dir_all(root).unwrap();
}
