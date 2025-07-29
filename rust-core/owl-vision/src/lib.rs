use ndarray::ArrayView3;
use opencv::{core, imgproc};
use std::ffi::c_void;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum VisionError {
    #[error("OpenCV error: {0}")]
    OpenCV(#[from] opencv::Error),
}

/// Convert an RGB image to grayscale.
pub fn rgb_to_gray(image: ArrayView3<'_, u8>) -> Result<core::Mat, VisionError> {
    let rows = image.shape()[0] as i32;
    let cols = image.shape()[1] as i32;
    if image.shape()[2] != 3 {
        return Err(VisionError::OpenCV(opencv::Error::new(0, "Invalid dimensions")));
    }
    // Create a mutable buffer from the image data
    let mut buffer = image.to_owned().into_raw_vec();
    let mat = core::Mat::new_rows_cols_with_data(
        rows,
        cols,
        core::CV_8UC3,
        &mut buffer,
        core::Mat_AUTO_STEP,
    )?;
    let mut gray = core::Mat::default();
    imgproc::cvt_color(&mat, &mut gray, imgproc::COLOR_RGB2GRAY, 0)?;
    Ok(gray)
}
