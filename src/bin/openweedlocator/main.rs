use std::env;
use log::LevelFilter;

fn init_logging() {
    let level = env::var("LOG_LEVEL")
        .ok()
        .and_then(|lvl| lvl.parse::<LevelFilter>().ok())
        .unwrap_or(LevelFilter::Debug);
    env_logger::Builder::new()
        .filter_level(level)
        .format_timestamp(None)
        .init();
}

fn main() {
    init_logging();
    println!("OpenWeedLocator starting with log level: {:?}", log::max_level());
}
