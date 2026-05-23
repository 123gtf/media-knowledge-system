-- ============================================================
-- 媒体知识库系统 MySQL 初始化脚本
-- ============================================================

CREATE DATABASE IF NOT EXISTS media_knowledge
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE media_knowledge;

-- -------------------------------------------
-- 原始媒体数据表
-- -------------------------------------------
CREATE TABLE raw_articles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(500) NOT NULL,
    content LONGTEXT NOT NULL,
    url VARCHAR(500) UNIQUE NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_type VARCHAR(50) DEFAULT 'web',
    publish_time DATETIME NOT NULL,
    fetch_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    checksum VARCHAR(64) NOT NULL,
    language VARCHAR(20) DEFAULT 'zh',
    raw_metadata JSON,
    INDEX idx_source_time (source, publish_time),
    INDEX idx_checksum (checksum),
    INDEX idx_fetch_time (fetch_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 清洗后的标准化数据
-- -------------------------------------------
CREATE TABLE cleaned_articles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    raw_article_id BIGINT NOT NULL UNIQUE,
    clean_title VARCHAR(500),
    clean_content LONGTEXT,
    clean_summary VARCHAR(1000),
    publish_time DATETIME,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    word_count INT DEFAULT 0,
    quality_score FLOAT DEFAULT 0.0,
    FOREIGN KEY (raw_article_id) REFERENCES raw_articles(id) ON DELETE CASCADE,
    INDEX idx_processed_at (processed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 抽取的实体索引
-- -------------------------------------------
CREATE TABLE entities (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(500) NOT NULL,
    entity_type ENUM('PER', 'ORG', 'LOC', 'TIME', 'EVENT', 'TOPIC') NOT NULL,
    aliases JSON,
    description TEXT,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    mention_count INT DEFAULT 1,
    confidence FLOAT DEFAULT 0.9,
    neo4j_id VARCHAR(100),
    UNIQUE KEY uk_name_type (name, entity_type),
    INDEX idx_type (entity_type),
    INDEX idx_confidence (confidence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 关系记录
-- -------------------------------------------
CREATE TABLE relations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    head_entity_id BIGINT NOT NULL,
    tail_entity_id BIGINT NOT NULL,
    relation_type VARCHAR(100) NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 0.8,
    evidence TEXT,
    source_article_id BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (head_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (tail_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (source_article_id) REFERENCES raw_articles(id) ON DELETE SET NULL,
    INDEX idx_relation_type (relation_type),
    INDEX idx_head_tail (head_entity_id, tail_entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- Agent 执行日志
-- -------------------------------------------
CREATE TABLE agent_execution_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id VARCHAR(100) NOT NULL,
    agent_type VARCHAR(50) NOT NULL,
    agent_name VARCHAR(100),
    action VARCHAR(200),
    status ENUM('PENDING', 'RUNNING', 'SUCCESS', 'FAILED') DEFAULT 'PENDING',
    input_data JSON,
    result JSON,
    error_message TEXT,
    duration_ms INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task_agent (task_id, agent_type),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 分析报告归档
-- -------------------------------------------
CREATE TABLE analysis_reports (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    report_id VARCHAR(100) UNIQUE NOT NULL,
    task_id VARCHAR(100) NOT NULL,
    report_title VARCHAR(500) NOT NULL,
    report_type ENUM('HOTSPOT', 'ENTITY_ANALYSIS', 'TOPIC_TREND', 'DATA_QUALITY') NOT NULL,
    markdown_content LONGTEXT,
    json_content JSON,
    coverage_start DATETIME,
    coverage_end DATETIME,
    article_count INT DEFAULT 0,
    entity_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_type_date (report_type, created_at),
    INDEX idx_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 质检记录
-- -------------------------------------------
CREATE TABLE quality_reviews (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id VARCHAR(100) NOT NULL,
    review_type VARCHAR(100),
    target_type VARCHAR(50),
    target_id BIGINT,
    passed BOOLEAN DEFAULT FALSE,
    issues JSON,
    reviewer_confidence FLOAT,
    corrective_action VARCHAR(500),
    corrective_taken BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task_id (task_id),
    INDEX idx_passed (passed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------
-- 任务黑板表 (用于 Agent 间协调)
-- -------------------------------------------
CREATE TABLE task_blackboard (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id VARCHAR(100) NOT NULL,
    parent_task_id VARCHAR(100),
    agent_type VARCHAR(50) NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    priority INT DEFAULT 5,
    status ENUM('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED') DEFAULT 'PENDING',
    input_data JSON,
    output_data JSON,
    dependencies JSON,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    completed_at DATETIME,
    INDEX idx_task_id (task_id),
    INDEX idx_status_priority (status, priority),
    INDEX idx_agent_type (agent_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
