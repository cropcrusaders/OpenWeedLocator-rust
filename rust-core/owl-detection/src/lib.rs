use ndarray::ArrayView3;
use opencv::{core, imgproc};
use std::ffi::c_void;

use thiserror::Error;

#[derive(Debug, Error)]
pub enum DetectionError {
    #[error("OpenCV error: {0}")]
    OpenCV(#[from] opencv::Error),
    #[error("Invalid image dimensions")]
    InvalidDimensions,
}

/// Simple green on brown detection using HSV threshold.
/// This is a placeholder implementation to demonstrate the
/// Rust integration. Performance optimisations using SIMD and
/// Rayon can be added here.
pub fn detect_green_on_brown(
    image: ArrayView3<'_, u8>,
    green_range: (u8, u8),
    pixel_count_threshold: Option<usize>,
) -> Result<usize, DetectionError> {
    let (h_min, h_max) = green_range;
    if image.shape()[2] != 3 {
        return Err(DetectionError::InvalidDimensions);
    }

    // Convert ndarray view into OpenCV Mat without copying
    let rows = image.shape()[0] as i32;
    let cols = image.shape()[1] as i32;
    // Create a new OpenCV Mat by copying the data from the ndarray
    let mat = core::Mat::from_slice_2d(
        &image
            .outer_iter()
            .map(|row| row.as_slice().unwrap())
            .collect::<Vec<_>>(),
    )?
    .reshape(3, rows)?;
    let mut hsv = core::Mat::default();
    imgproc::cvt_color(&mat, &mut hsv, imgproc::COLOR_RGB2HSV, 0)?;

    let lower = core::Scalar::new(h_min as f64, 50.0, 50.0, 0.0);
    let upper = core::Scalar::new(h_max as f64, 255.0, 255.0, 0.0);
    let mut mask = core::Mat::default();
    core::in_range(&hsv, &lower, &upper, &mut mask)?;

    let count = core::count_non_zero(&mask)? as usize;
    if let Some(threshold) = pixel_count_threshold {
        if count < threshold {
            return Ok(0);
        }
    }
    Ok(count)
}
