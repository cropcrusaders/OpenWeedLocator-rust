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
}
