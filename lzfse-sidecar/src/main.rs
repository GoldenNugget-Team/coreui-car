//! stdio sidecar: reads an LZFSE byte stream on stdin and writes the
//! decompressed bytes to stdout, preserving them exactly.
//!
//! Uses the official `lzfse_rust` crate so the Python library can decode
//! LZFSE renditions ("compression code 4") without a pure-Python port of the
//! FSE entropy coder.
use std::io::{Read, Write};

fn run() -> Result<(), String> {
    let mut src = Vec::new();
    std::io::stdin().read_to_end(&mut src).map_err(|e| e.to_string())?;

    let mut out = Vec::new();
    let mut decoder = lzfse_rust::LzfseDecoder::default();
    let n = decoder
        .decode_bytes(&src, &mut out)
        .map_err(|e| format!("lzfse decode failed: {e:?}"))?;
    out.truncate(n as usize);

    if n < u64::from(src.len() as u32) {
        // Multi-member LZFSE streams are rare; reject truncated input loudly
        // so byte-accounting bugs surface in tests.
        if n != src.len() as u64 {
            return Err(format!(
                "lzfse sidecar consumed {n} of {} input bytes",
                src.len()
            ));
        }
    }

    std::io::stdout()
        .write_all(&out)
        .map_err(|e| format!("stdout write failed: {e}"))
}

fn main() {
    if let Err(message) = run() {
        eprintln!("lzfse-sidecar: {message}");
        std::process::exit(1);
    }
}
