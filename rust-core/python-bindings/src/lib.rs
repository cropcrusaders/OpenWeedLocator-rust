use numpy::{PyArray3, IntoPyArray};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use owl_detection::detect_green_on_brown;
use owl_hardware::trigger_relay;

#[pyfunction]
#[pyo3(signature = (image, green_range, pixel_count_threshold=None, enable_rust=true))]
fn detect_green_on_brown_py(
    py: Python<'_>,
    image: &PyArray3<u8>,
    green_range: (u8, u8),
    pixel_count_threshold: Option<usize>,
    enable_rust: bool,
) -> PyResult<PyObject> {
    if !enable_rust {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Rust disabled"));
    }
    let view = image.to_owned_array()?;
    match detect_green_on_brown(&view, green_range, pixel_count_threshold) {
        Ok(count) => {
            let dict = PyDict::new(py);
            dict.set_item("count", count)?;
            Ok(dict.into())
        }
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string())),
    }
}

#[pyfunction]
fn trigger_relay_py(id: u8, duration_ms: u64) -> PyResult<()> {
    trigger_relay(id, duration_ms).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

#[pymodule]
fn owl_rust(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(detect_green_on_brown_py, m)?)?;
    m.add_function(wrap_pyfunction!(trigger_relay_py, m)?)?;
    Ok(())
}
