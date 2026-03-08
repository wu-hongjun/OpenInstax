use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=cbindgen.toml");

    let crate_dir =
        env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR must be set by Cargo");
    let crate_path = PathBuf::from(&crate_dir);
    let output_dir = crate_path.join("include");
    std::fs::create_dir_all(&output_dir).expect("failed to create include/ directory");

    cbindgen::Builder::new()
        .with_crate(&crate_dir)
        .with_config(
            cbindgen::Config::from_file(crate_path.join("cbindgen.toml"))
                .expect("failed to load cbindgen.toml"),
        )
        .generate()
        .expect("Unable to generate C bindings")
        .write_to_file(output_dir.join("openinstax.h"));
}
