use std::path::PathBuf;

use crate::error::{Result, YacliError};

pub fn config_dir() -> Result<PathBuf> {
    if let Some(value) = std::env::var_os("YACLI_CONFIG_DIR") {
        return Ok(PathBuf::from(value));
    }

    let base = dirs::config_dir()
        .ok_or_else(|| YacliError::Config("Could not resolve config directory".to_string()))?;
    Ok(base.join("yacli"))
}

pub fn accounts_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("accounts.toml"))
}

pub fn credentials_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("credentials.toml"))
}

pub fn activity_log_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("activity.toml"))
}

pub fn mail_rules_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("mail-rules.toml"))
}

pub fn mail_rules_state_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("mail-rules-state.toml"))
}

pub fn mail_rules_audit_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("mail-rules-audit.toml"))
}

pub fn oauth_sessions_path() -> Result<PathBuf> {
    Ok(config_dir()?.join("oauth_sessions.toml"))
}
