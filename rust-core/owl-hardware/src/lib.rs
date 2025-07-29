use thiserror::Error;

#[derive(Debug, Error)]
pub enum HardwareError {
    #[error("GPIO error: {0}")]
    Gpio(String),
}

/// Simulated GPIO control for demonstration.
pub fn trigger_relay(id: u8, duration_ms: u64) -> Result<(), HardwareError> {
    // Placeholder: integrate with rppal or wiringPi in future.
    println!("Activating relay {} for {} ms", id, duration_ms);
    std::thread::sleep(std::time::Duration::from_millis(duration_ms));
    Ok(())
}
