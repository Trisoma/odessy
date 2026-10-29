//! Experimental code authentication: upstream SPAKE2, then Noise NNpsk0.
//! No certificate is trusted until code authentication and local approval finish.
use super::exchange::Channel;
use super::profile::{hex,unhex};
use serde_json::json;
use spake2::{Ed25519Group,Identity,Password,Spake2};
const PROTOCOL:&str="odessy-pair-spake2-noise-v1";
const NOISE:&str="Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s";

fn blob(value:&serde_json::Value,key:&str,max:usize)->Result<Vec<u8>,String>{
    let text=value.get(key).and_then(|x|x.as_str()).ok_or("Missing handshake field")?;
    if text.len()>max*2{return Err("Handshake limit exceeded".into());}
    unhex(text).ok_or("Invalid handshake encoding".into())
}
fn context(session:&str,host:&[u8],joiner:&[u8])->Vec<u8>{
    // Unambiguous length-prefixed public transcript; Noise binds it as prologue.
    let mut output=Vec::new();
    for part in [PROTOCOL.as_bytes(),session.as_bytes(),host,joiner]{
        output.extend_from_slice(&(part.len() as u32).to_be_bytes());output.extend_from_slice(part);
    }
    output
}
fn noise(key:&[u8],context:&[u8],initiator:bool)->Result<snow::HandshakeState,String>{
    let builder=snow::Builder::new(NOISE.parse().map_err(|_|"Invalid Noise pattern")?)
        .prologue(context).psk(0,key);
    if initiator{builder.build_initiator()}else{builder.build_responder()}.map_err(|_|"Cannot initialize protected pairing channel".into())
}
pub fn host(channel:&mut Channel,session:&str,code:&str)->Result<(),String>{
    let (state,host)=Spake2::<Ed25519Group>::start_a(&Password::new(code.as_bytes()),&Identity::new(b"odessy/host/v1"),&Identity::new(b"odessy/joiner/v1"));
    channel.send_raw(&json!({"protocol":PROTOCOL,"session":session,"pake":hex(&host)}))?;
    let value=channel.receive_raw()?;
    if value.get("protocol").and_then(|x|x.as_str())!=Some(PROTOCOL){return Err("Unsupported pairing protocol".into());}
    let peer=blob(&value,"pake",33)?;
    let key=state.finish(&peer).map_err(|_|"Code authentication failed")?;
    let mut handshake=noise(&key,&context(session,&host,&peer),false)?;
    let mut output=[0u8;1024];
    handshake.read_message(&blob(&channel.receive_raw()?,"noise",1024)?,&mut output).map_err(|_|"Code authentication failed")?;
    let count=handshake.write_message(&[],&mut output).map_err(|_|"Pairing handshake failed")?;
    channel.send_raw(&json!({"noise":hex(&output[..count])}))?;
    channel.protect(handshake.into_transport_mode().map_err(|_|"Pairing handshake incomplete")?);
    let confirmation=channel.receive()?;
    if confirmation!=json!({"confirm":"joiner"}){return Err("Pairing confirmation failed".into());}
    channel.send(&json!({"confirm":"host"}))
}
pub fn join(channel:&mut Channel,code:&str)->Result<(),String>{
    let value=channel.receive_raw()?;
    if value.get("protocol").and_then(|x|x.as_str())!=Some(PROTOCOL){return Err("Unsupported pairing protocol".into());}
    let session=value.get("session").and_then(|x|x.as_str()).ok_or("Missing pairing window")?;
    if session.len()!=32 || !session.bytes().all(|b|b.is_ascii_hexdigit()){return Err("Invalid pairing window".into());}
    let host=blob(&value,"pake",33)?;
    let (state,peer)=Spake2::<Ed25519Group>::start_b(&Password::new(code.as_bytes()),&Identity::new(b"odessy/host/v1"),&Identity::new(b"odessy/joiner/v1"));
    let key=state.finish(&host).map_err(|_|"Code authentication failed")?;
    channel.send_raw(&json!({"protocol":PROTOCOL,"pake":hex(&peer)}))?;
    let mut handshake=noise(&key,&context(session,&host,&peer),true)?;
    let mut output=[0u8;1024];
    let count=handshake.write_message(&[],&mut output).map_err(|_|"Pairing handshake failed")?;
    channel.send_raw(&json!({"noise":hex(&output[..count])}))?;
    handshake.read_message(&blob(&channel.receive_raw()?,"noise",1024)?,&mut output).map_err(|_|"Code authentication failed")?;
    channel.protect(handshake.into_transport_mode().map_err(|_|"Pairing handshake incomplete")?);
    channel.send(&json!({"confirm":"joiner"}))?;
    if channel.receive()?!=json!({"confirm":"host"}){return Err("Pairing confirmation failed".into());}
    Ok(())
}

#[cfg(test)]mod tests{
    use super::*;
    use std::net::{TcpListener,TcpStream};
    #[test]fn codes_authenticate_before_identity_exchange(){
        for code in ["012345","999999"]{
            let listener=TcpListener::bind("127.0.0.1:0").unwrap();let address=listener.local_addr().unwrap();
            let worker=std::thread::spawn(move||{let mut c=Channel::wrap(listener.accept().unwrap().0).unwrap();host(&mut c,"00112233445566778899aabbccddeeff","012345")});
            let mut c=Channel::wrap(TcpStream::connect(address).unwrap()).unwrap();
            let result=join(&mut c,code);
            assert_eq!(result.is_ok(),code=="012345");assert_eq!(worker.join().unwrap().is_ok(),code=="012345");
        }
    }
}
