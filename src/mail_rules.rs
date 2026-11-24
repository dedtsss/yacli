use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::{Result, YacliError};
use crate::mail::{MailMessageSummary, MailboxCheckpoint};
use crate::persist::write_config_file;

pub const MAIL_RULES_VERSION: u32 = 1;
pub const MAX_MAIL_RULES: usize = 100;
pub const MAX_RULE_BATCH: usize = 50;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MailRuleAction {
    MarkRead,
    MarkUnread,
    Star,
    Unstar,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRule {
    pub id: String,
    #[serde(default)]
    pub enabled: bool,
    pub account: String,
    #[serde(default = "default_mailbox")]
    pub mailbox: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_contains: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject_contains: Option<String>,
    pub action: MailRuleAction,
}

fn default_mailbox() -> String {
    "INBOX".into()
}

impl MailRule {
    pub fn validate(&self) -> Result<()> {
        if self.id.is_empty()
            || self.id.len() > 64
            || !self
                .id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(YacliError::Validation(
                "ID правила должен содержать 1–64 ASCII-букв, цифр, дефисов или подчёркиваний"
                    .into(),
            ));
        }
        if self.account.trim() != self.account
            || self.account.is_empty()
            || self.account.len() > 128
        {
            return Err(YacliError::Validation(
                "У правила должен быть точный непустой alias аккаунта".into(),
            ));
        }
        if self.mailbox.trim() != self.mailbox
            || self.mailbox.is_empty()
            || self.mailbox.len() > 512
            || self.mailbox.chars().any(char::is_control)
        {
            return Err(YacliError::Validation(
                "У правила должна быть точная непустая IMAP-папка".into(),
            ));
        }
        let from = self.from_contains.as_deref().unwrap_or_default();
        let subject = self.subject_contains.as_deref().unwrap_or_default();
        if from.is_empty() && subject.is_empty() {
            return Err(YacliError::Validation(
                "Правило должно содержать хотя бы одно условие From или Subject".into(),
            ));
        }
        if from.len() > 256 || subject.len() > 256 {
            return Err(YacliError::Validation(
                "Условие правила не может быть длиннее 256 байт".into(),
            ));
        }
        Ok(())
    }

    pub(crate) fn matches(&self, message: &MailMessageSummary) -> bool {
        let from_matches = self.from_contains.as_deref().is_none_or(|needle| {
            message
                .from
                .as_deref()
                .is_some_and(|value| value.to_lowercase().contains(&needle.to_lowercase()))
        });
        let subject_matches = self.subject_contains.as_deref().is_none_or(|needle| {
            message
                .subject
                .as_deref()
                .is_some_and(|value| value.to_lowercase().contains(&needle.to_lowercase()))
        });
        from_matches && subject_matches
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MailRulePreview {
    pub rule_id: String,
    pub account: String,
    pub mailbox: String,
    pub uid_validity: u64,
    pub after_uid: u64,
    pub target_cursor_uid: u64,
    pub action: MailRuleAction,
    pub messages: Vec<MailMessageSummary>,
    pub has_more: bool,
    pub review_sha256: String,
}

impl MailRulePreview {
    pub fn new(
        rule: &MailRule,
        previous: &MailRuleCheckpoint,
        current: &MailboxCheckpoint,
        messages: Vec<MailMessageSummary>,
        has_more: bool,
    ) -> Result<Self> {
        rule.validate()?;
        if !rule.enabled {
            return Err(YacliError::Validation(format!(
                "Правило {} выключено",
                rule.id
            )));
        }
        if previous.rule_id != rule.id
            || previous.account != rule.account
            || previous.mailbox != rule.mailbox
            || current.folder != rule.mailbox
        {
            return Err(YacliError::Validation(
                "Контрольная точка не соответствует правилу".into(),
            ));
        }
        if current.uid_validity != previous.uid_validity {
            return Err(YacliError::Validation(
                "UIDVALIDITY изменилась; автоматическое правило остановлено до проверки".into(),
            ));
        }
        if current.uid_next == 0 {
            return Err(YacliError::Validation(
                "IMAP сообщил некорректный UIDNEXT".into(),
            ));
        }
        if messages.len() > MAX_RULE_BATCH
            || messages.iter().any(|message| {
                message.uid <= previous.cursor_uid
                    || message.uid_validity != Some(previous.uid_validity)
            })
        {
            return Err(YacliError::Validation(
                "Результат IMAP не соответствует контрольной точке правила".into(),
            ));
        }
        let target_cursor_uid = if has_more {
            messages
                .last()
                .map(|message| message.uid)
                .unwrap_or(previous.cursor_uid)
        } else {
            current.uid_next.saturating_sub(1).max(previous.cursor_uid)
        };
        let messages = messages
            .into_iter()
            .filter(|message| rule.matches(message))
            .collect::<Vec<_>>();
        let review_sha256 = rule_review_digest(
            rule,
            previous.uid_validity,
            previous.cursor_uid,
            target_cursor_uid,
            &messages,
        )?;
        Ok(Self {
            rule_id: rule.id.clone(),
            account: rule.account.clone(),
            mailbox: rule.mailbox.clone(),
            uid_validity: previous.uid_validity,
            after_uid: previous.cursor_uid,
            target_cursor_uid,
            action: rule.action.clone(),
            messages,
            has_more,
            review_sha256,
        })
    }

    pub fn pending_action(&self) -> Result<Option<PendingRuleAction>> {
        let messages = self
            .messages
            .iter()
            .map(|message| {
                Ok(PendingRuleMessage {
                    uid: message.uid,
                    was_seen: has_flag(message, "\\Seen"),
                    was_flagged: has_flag(message, "\\Flagged"),
                    source_sha256: rule_source_digest(message)?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        Ok((!messages.is_empty()).then(|| PendingRuleAction {
            action: self.action.clone(),
            messages,
            target_cursor_uid: self.target_cursor_uid,
            review_sha256: self.review_sha256.clone(),
        }))
    }
}

fn rule_review_digest(
    rule: &MailRule,
    uid_validity: u64,
    after_uid: u64,
    target_cursor_uid: u64,
    messages: &[MailMessageSummary],
) -> Result<String> {
    let encoded = serde_json::to_vec(&(rule, uid_validity, after_uid, target_cursor_uid, messages))
        .map_err(|error| YacliError::Serialization(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

pub(crate) fn has_flag(message: &MailMessageSummary, flag: &str) -> bool {
    message
        .flags
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(flag))
}

pub(crate) fn rule_source_digest(message: &MailMessageSummary) -> Result<String> {
    let encoded = serde_json::to_vec(&(
        message.uid,
        &message.from,
        &message.subject,
        &message.date,
        message.size,
    ))
    .map_err(|error| YacliError::Serialization(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRulesFile {
    pub version: u32,
    #[serde(default)]
    pub rules: Vec<MailRule>,
}

impl Default for MailRulesFile {
    fn default() -> Self {
        Self {
            version: MAIL_RULES_VERSION,
            rules: Vec::new(),
        }
    }
}

impl MailRulesFile {
    pub fn validate(&self) -> Result<()> {
        if self.version != MAIL_RULES_VERSION {
            return Err(YacliError::Config(format!(
                "Неподдерживаемая версия mail-rules.toml: {}",
                self.version
            )));
        }
        if self.rules.len() > MAX_MAIL_RULES {
            return Err(YacliError::Validation(format!(
                "Разрешено не более {MAX_MAIL_RULES} правил"
            )));
        }
        let mut ids = BTreeSet::new();
        for rule in &self.rules {
            rule.validate()?;
            if !ids.insert(&rule.id) {
                return Err(YacliError::Validation(format!(
                    "Повторяется ID правила {}",
                    rule.id
                )));
            }
        }
        Ok(())
    }

    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let contents = fs::read_to_string(path)?;
        let file = toml::from_str::<Self>(&contents)?;
        file.validate()?;
        Ok(file)
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        self.validate()?;
        let contents = toml::to_string_pretty(self)?;
        write_config_file(path, &contents)
    }

    pub fn enabled_rules(&self) -> impl Iterator<Item = &MailRule> {
        self.rules.iter().filter(|rule| rule.enabled)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRulesState {
    pub version: u32,
    #[serde(default)]
    pub checkpoints: Vec<MailRuleCheckpoint>,
}

impl Default for MailRulesState {
    fn default() -> Self {
        Self {
            version: MAIL_RULES_VERSION,
            checkpoints: Vec::new(),
        }
    }
}

impl MailRulesState {
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let contents = fs::read_to_string(path)?;
        let state = toml::from_str::<Self>(&contents)?;
        state.validate()?;
        Ok(state)
    }

    pub fn validate(&self) -> Result<()> {
        if self.version != MAIL_RULES_VERSION {
            return Err(YacliError::Config(format!(
                "Неподдерживаемая версия mail-rules-state.toml: {}",
                self.version
            )));
        }
        let mut ids = BTreeSet::new();
        for checkpoint in &self.checkpoints {
            if checkpoint.rule_id.is_empty()
                || checkpoint.uid_validity == 0
                || !ids.insert(&checkpoint.rule_id)
            {
                return Err(YacliError::Config(
                    "Повреждена контрольная точка правила или повторяется её ID".into(),
                ));
            }
            if let Some(pending) = &checkpoint.pending {
                if pending.messages.is_empty()
                    || pending.messages.len() > MAX_RULE_BATCH
                    || pending.messages.iter().any(|message| message.uid == 0)
                    || pending.messages.iter().any(|message| {
                        message.source_sha256.len() != 64
                            || !message
                                .source_sha256
                                .bytes()
                                .all(|byte| byte.is_ascii_hexdigit())
                    })
                    || !pending
                        .messages
                        .windows(2)
                        .all(|messages| messages[0].uid < messages[1].uid)
                    || pending.target_cursor_uid < checkpoint.cursor_uid
                    || pending.review_sha256.len() != 64
                    || !pending
                        .review_sha256
                        .bytes()
                        .all(|byte| byte.is_ascii_hexdigit())
                {
                    return Err(YacliError::Config(
                        "Повреждён незавершённый пакет правила".into(),
                    ));
                }
            }
        }
        Ok(())
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        self.validate()?;
        let contents = toml::to_string_pretty(self)?;
        write_config_file(path, &contents)
    }

    pub fn checkpoint(&self, rule_id: &str) -> Option<&MailRuleCheckpoint> {
        self.checkpoints
            .iter()
            .find(|checkpoint| checkpoint.rule_id == rule_id)
    }

    pub fn checkpoint_mut(&mut self, rule_id: &str) -> Option<&mut MailRuleCheckpoint> {
        self.checkpoints
            .iter_mut()
            .find(|checkpoint| checkpoint.rule_id == rule_id)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRuleCheckpoint {
    pub rule_id: String,
    pub account: String,
    pub mailbox: String,
    pub uid_validity: u64,
    pub cursor_uid: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pending: Option<PendingRuleAction>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingRuleAction {
    pub action: MailRuleAction,
    pub messages: Vec<PendingRuleMessage>,
    pub target_cursor_uid: u64,
    pub review_sha256: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PendingRuleMessage {
    pub uid: u64,
    pub was_seen: bool,
    pub was_flagged: bool,
    pub source_sha256: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRulesAuditFile {
    pub version: u32,
    #[serde(default)]
    pub runs: Vec<MailRuleAuditRecord>,
}

impl Default for MailRulesAuditFile {
    fn default() -> Self {
        Self {
            version: MAIL_RULES_VERSION,
            runs: Vec::new(),
        }
    }
}

impl MailRulesAuditFile {
    pub fn load(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let contents = fs::read_to_string(path)?;
        let file = toml::from_str::<Self>(&contents)?;
        file.validate()?;
        Ok(file)
    }

    pub fn validate(&self) -> Result<()> {
        if self.version != MAIL_RULES_VERSION {
            return Err(YacliError::Config(
                "Неподдерживаемая версия mail-rules-audit.toml".into(),
            ));
        }
        if self.runs.len() > 1000 {
            return Err(YacliError::Config(
                "Журнал локальных правил превышает 1000 записей".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for run in &self.runs {
            if run.run_id.is_empty()
                || run.run_id.len() > 128
                || !ids.insert(&run.run_id)
                || run.rule_id.is_empty()
                || run.uid_validity == 0
                || run.messages.is_empty()
                || run.messages.len() > MAX_RULE_BATCH
                || run.messages.iter().any(|message| message.uid == 0)
                || !run
                    .messages
                    .windows(2)
                    .all(|pair| pair[0].uid < pair[1].uid)
                || run.undo_pending.as_ref().is_some_and(|pending| {
                    pending.messages.is_empty()
                        || pending.messages.len() > MAX_RULE_BATCH
                        || pending.messages.iter().any(|message| message.uid == 0)
                        || !pending
                            .messages
                            .windows(2)
                            .all(|pair| pair[0].uid < pair[1].uid)
                })
            {
                return Err(YacliError::Config(
                    "Повреждена запись аудита локальных правил".into(),
                ));
            }
        }
        Ok(())
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        self.validate()?;
        let contents = toml::to_string_pretty(self)?;
        write_config_file(path, &contents)
    }

    pub fn record(&mut self, record: MailRuleAuditRecord) {
        self.runs
            .retain(|existing| existing.run_id != record.run_id);
        self.runs.push(record);
        if self.runs.len() > 1000 {
            let excess = self.runs.len() - 1000;
            self.runs.drain(..excess);
        }
    }

    pub fn run(&self, run_id: &str) -> Option<&MailRuleAuditRecord> {
        self.runs.iter().find(|record| record.run_id == run_id)
    }

    pub fn run_mut(&mut self, run_id: &str) -> Option<&mut MailRuleAuditRecord> {
        self.runs.iter_mut().find(|record| record.run_id == run_id)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRuleAuditRecord {
    pub run_id: String,
    pub rule_id: String,
    pub account: String,
    pub mailbox: String,
    pub uid_validity: u64,
    pub action: MailRuleAction,
    pub applied_at_epoch_secs: u64,
    pub messages: Vec<MailRuleAuditMessage>,
    #[serde(default)]
    pub undone_at_epoch_secs: Option<u64>,
    #[serde(default)]
    pub undo_pending: Option<MailRuleUndoPending>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRuleAuditMessage {
    pub uid: u64,
    pub was_seen: bool,
    pub was_flagged: bool,
    pub became_seen: bool,
    pub became_flagged: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRuleUndoPending {
    pub review_sha256: String,
    pub messages: Vec<MailRuleUndoMessage>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MailRuleUndoMessage {
    pub uid: u64,
    pub expected_seen: Option<bool>,
    pub restore_seen: Option<bool>,
    pub expected_flagged: Option<bool>,
    pub restore_flagged: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MailRuleUndoPreview {
    pub run_id: String,
    pub account: String,
    pub mailbox: String,
    pub uid_validity: u64,
    pub messages: Vec<MailRuleUndoMessage>,
    pub review_sha256: String,
}

impl MailRuleUndoPreview {
    pub fn new(
        record: &MailRuleAuditRecord,
        current_messages: &[MailMessageSummary],
    ) -> Result<Self> {
        if record.undone_at_epoch_secs.is_some() || record.undo_pending.is_some() {
            return Err(YacliError::Validation(
                "Для этого запуска уже выполнен или ожидает продолжения откат".into(),
            ));
        }
        if current_messages.len() != record.messages.len() {
            return Err(YacliError::Validation(
                "Состав писем после правила изменился; откат остановлен".into(),
            ));
        }
        let mut messages = Vec::with_capacity(record.messages.len());
        for (applied, current) in record.messages.iter().zip(current_messages) {
            if applied.uid != current.uid
                || current.uid_validity != Some(record.uid_validity)
                || match record.action {
                    MailRuleAction::MarkRead | MailRuleAction::MarkUnread => {
                        has_flag(current, "\\Seen") != applied.became_seen
                    }
                    MailRuleAction::Star | MailRuleAction::Unstar => {
                        has_flag(current, "\\Flagged") != applied.became_flagged
                    }
                }
            {
                return Err(YacliError::Validation(format!(
                    "UID {} изменён после правила; безопасный откат невозможен",
                    applied.uid
                )));
            }
            let (expected_seen, restore_seen, expected_flagged, restore_flagged) =
                match record.action {
                    MailRuleAction::MarkRead | MailRuleAction::MarkUnread => (
                        Some(applied.became_seen),
                        Some(applied.was_seen),
                        None,
                        None,
                    ),
                    MailRuleAction::Star | MailRuleAction::Unstar => (
                        None,
                        None,
                        Some(applied.became_flagged),
                        Some(applied.was_flagged),
                    ),
                };
            messages.push(MailRuleUndoMessage {
                uid: applied.uid,
                expected_seen,
                restore_seen,
                expected_flagged,
                restore_flagged,
            });
        }
        let digest = serde_json::to_vec(&(record, &messages))
            .map_err(|error| YacliError::Serialization(error.to_string()))?;
        Ok(Self {
            run_id: record.run_id.clone(),
            account: record.account.clone(),
            mailbox: record.mailbox.clone(),
            uid_validity: record.uid_validity,
            messages,
            review_sha256: format!("{:x}", Sha256::digest(digest)),
        })
    }

    pub fn into_pending(self) -> MailRuleUndoPending {
        MailRuleUndoPending {
            review_sha256: self.review_sha256,
            messages: self.messages,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule() -> MailRule {
        MailRule {
            id: "vendor-invoices".into(),
            enabled: false,
            account: "dedts".into(),
            mailbox: "INBOX".into(),
            from_contains: Some("billing@example.test".into()),
            subject_contains: Some("invoice".into()),
            action: MailRuleAction::MarkRead,
        }
    }

    #[test]
    fn rule_requires_at_least_one_condition_and_exact_identity() {
        let mut value = rule();
        value.from_contains = None;
        value.subject_contains = None;
        assert!(value.validate().is_err());
        value.from_contains = Some("x".into());
        value.account = " dedts".into();
        assert!(value.validate().is_err());
    }

    #[test]
    fn rule_conditions_are_case_insensitive_and_conjunctive() {
        let value = rule();
        let message = MailMessageSummary {
            uid: 12,
            uid_validity: Some(1),
            subject: Some("March INVOICE".into()),
            from: Some("Billing@example.test".into()),
            date: None,
            flags: Vec::new(),
            size: None,
        };
        assert!(value.matches(&message));
        let mut miss = message.clone();
        miss.from = Some("other@example.test".into());
        assert!(!value.matches(&miss));
    }

    #[test]
    fn rules_are_disabled_by_default_and_unknown_fields_fail_closed() {
        let file = MailRulesFile {
            version: 1,
            rules: vec![rule()],
        };
        assert_eq!(file.enabled_rules().count(), 0);
        assert!(toml::from_str::<MailRule>(
            "id='x'\nenabled=false\naccount='dedts'\nmailbox='INBOX'\nfrom_contains='x'\naction='mark_read'\nsecret='unexpected'"
        )
        .is_err());
    }

    #[test]
    fn rules_and_state_use_independent_secure_files() {
        use tempfile::tempdir;

        let dir = tempdir().expect("tempdir");
        let rules_path = dir.path().join("mail-rules.toml");
        let state_path = dir.path().join("mail-rules-state.toml");
        let legacy_path = dir.path().join("state.json");
        std::fs::write(&legacy_path, "{\"legacy\":true}").expect("legacy fixture");
        let file = MailRulesFile {
            rules: vec![rule()],
            ..MailRulesFile::default()
        };
        file.save(&rules_path).expect("save rules");
        MailRulesState::default()
            .save(&state_path)
            .expect("save state");
        assert_eq!(MailRulesFile::load(&rules_path).expect("load rules"), file);
        assert_eq!(
            std::fs::read_to_string(&legacy_path).expect("legacy unchanged"),
            "{\"legacy\":true}"
        );
    }

    #[test]
    fn rules_reject_duplicate_ids_and_excessive_pending_batches() {
        let file = MailRulesFile {
            rules: vec![rule(), rule()],
            ..MailRulesFile::default()
        };
        assert!(file.validate().is_err());
        let mut state = MailRulesState::default();
        state.checkpoints.push(MailRuleCheckpoint {
            rule_id: "vendor-invoices".into(),
            account: "dedts".into(),
            mailbox: "INBOX".into(),
            uid_validity: 1,
            cursor_uid: 1,
            pending: Some(PendingRuleAction {
                action: MailRuleAction::MarkRead,
                messages: (2..=MAX_RULE_BATCH as u64 + 2)
                    .map(|uid| PendingRuleMessage {
                        uid,
                        was_seen: false,
                        was_flagged: false,
                        source_sha256: "0".repeat(64),
                    })
                    .collect(),
                target_cursor_uid: MAX_RULE_BATCH as u64 + 2,
                review_sha256: "0".repeat(64),
            }),
        });
        assert!(state.validate().is_err());
    }

    #[test]
    fn preview_binds_rule_epoch_cursor_and_matching_messages() {
        let mut active = rule();
        active.enabled = true;
        let previous = MailRuleCheckpoint {
            rule_id: active.id.clone(),
            account: active.account.clone(),
            mailbox: active.mailbox.clone(),
            uid_validity: 20,
            cursor_uid: 10,
            pending: None,
        };
        let current = MailboxCheckpoint {
            folder: "INBOX".into(),
            uid_validity: 20,
            uid_next: 15,
        };
        let message = MailMessageSummary {
            uid: 12,
            uid_validity: Some(20),
            subject: Some("Invoice".into()),
            from: Some("billing@example.test".into()),
            date: None,
            flags: vec!["\\Flagged".into()],
            size: Some(10),
        };
        let preview =
            MailRulePreview::new(&active, &previous, &current, vec![message.clone()], false)
                .expect("preview");
        assert_eq!(preview.target_cursor_uid, 14);
        assert_eq!(preview.messages.len(), 1);
        assert_eq!(
            preview
                .pending_action()
                .expect("valid pending")
                .expect("pending")
                .messages[0]
                .uid,
            12
        );

        let changed_epoch = MailboxCheckpoint {
            uid_validity: 21,
            ..current
        };
        assert!(
            MailRulePreview::new(&active, &previous, &changed_epoch, vec![message], false,)
                .is_err()
        );
    }

    #[test]
    fn undo_preview_rejects_changed_target_flags_and_binds_uid_epoch() {
        let record = MailRuleAuditRecord {
            run_id: "a".repeat(64),
            rule_id: "vendor-invoices".into(),
            account: "dedts".into(),
            mailbox: "INBOX".into(),
            uid_validity: 20,
            action: MailRuleAction::MarkRead,
            applied_at_epoch_secs: 1,
            messages: vec![MailRuleAuditMessage {
                uid: 12,
                was_seen: false,
                was_flagged: false,
                became_seen: true,
                became_flagged: false,
            }],
            undone_at_epoch_secs: None,
            undo_pending: None,
        };
        let current = MailMessageSummary {
            uid: 12,
            uid_validity: Some(20),
            subject: Some("Invoice".into()),
            from: Some("billing@example.test".into()),
            date: None,
            flags: vec!["\\Seen".into(), "\\Flagged".into()],
            size: Some(10),
        };
        let preview = MailRuleUndoPreview::new(&record, std::slice::from_ref(&current))
            .expect("undo preview");
        assert_eq!(preview.messages[0].restore_seen, Some(false));
        assert_eq!(preview.messages[0].restore_flagged, None);
        assert_eq!(preview.review_sha256.len(), 64);

        let changed = MailMessageSummary {
            flags: Vec::new(),
            ..current
        };
        assert!(MailRuleUndoPreview::new(&record, &[changed]).is_err());
    }

    #[test]
    fn audit_rejects_duplicate_runs_and_oversized_history() {
        let file = MailRulesAuditFile {
            runs: vec![audit_record("run-1"), audit_record("run-1")],
            ..MailRulesAuditFile::default()
        };
        assert!(file.validate().is_err());

        let file = MailRulesAuditFile {
            runs: (0..=1000)
                .map(|index| audit_record(&format!("run-{index}")))
                .collect(),
            ..MailRulesAuditFile::default()
        };
        assert!(file.validate().is_err());
    }

    fn audit_record(run_id: &str) -> MailRuleAuditRecord {
        MailRuleAuditRecord {
            run_id: run_id.into(),
            rule_id: "test".into(),
            account: "dedts".into(),
            mailbox: "INBOX".into(),
            uid_validity: 1,
            action: MailRuleAction::MarkRead,
            applied_at_epoch_secs: 1,
            messages: vec![MailRuleAuditMessage {
                uid: 1,
                was_seen: false,
                was_flagged: false,
                became_seen: true,
                became_flagged: false,
            }],
            undone_at_epoch_secs: None,
            undo_pending: None,
        }
    }
}
