//! Short-code pairing.
//!
//! Real enrollment is unavailable until M0 approves a complete protocol profile
//! (`docs/security/m0-pairing-profile.md`). What is implemented here is everything
//! around that decision: a durable session with a six-digit code and a 120-second
//! window, a global attempt budget reserved atomically, bounded frames and aggregate
//! bytes, pending-versus-active trust, a staged commit with durable operation ids,
//! and recovery that reports an interrupted commit instead of completing it.
//!
//! The only executable exchange is an explicitly unapproved test adapter that needs
//! both the `test-profile` cargo feature and `ODESSY_LINK_TEST_PAIRING_ADAPTER=1`.
//! A release build has no such code path, and every response it produces says so.

mod cli;
mod exchange;
mod profile;
mod store;

pub use cli::run;
#[cfg(test)]
pub use cli::{capabilities, serve, snapshot};

#[cfg(test)]
mod lifecycle_tests;

