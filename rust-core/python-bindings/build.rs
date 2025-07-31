use std::process::Command;

fn main() {
    if Command::new("pkg-config").arg("--libs").arg("opencv4").output().is_err() {
        eprintln!("error: OpenCV development libraries not found. Install 'libopencv-dev' or set OPENCV_VENDORED=1 to build from source.");
        std::process::exit(1);
    }
    if Command::new("clang").arg("--version").output().is_err() {
        eprintln!("error: clang is required to build the Rust OpenCV bindings. Please install clang and libclang-dev.");
        std::process::exit(1);
    }

    // ensure libclang is discoverable
    if std::env::var("LIBCLANG_PATH").is_err() {
        let common_paths = vec![
            "/usr/lib/llvm-18/lib",
            "/usr/lib/llvm-17/lib",
            "/usr/lib/llvm-16/lib",
            "/usr/local/lib",
            "/opt/homebrew/opt/llvm/lib", // Common on macOS with Homebrew
        ];
        let libclang_path = common_paths.iter()
            .find(|path| std::path::Path::new(path).exists());
        if libclang_path.is_none() {
            eprintln!("error: libclang not found. Set LIBCLANG_PATH to the directory containing libclang.so");
            std::process::exit(1);
        }
    }
}
