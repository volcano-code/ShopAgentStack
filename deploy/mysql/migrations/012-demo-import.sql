-- Original ShopAgentStack: opt-in demo seed plans. No automatic fixture import.
CREATE TABLE IF NOT EXISTS shop_agent_stack_demo_seed (
 id INT PRIMARY KEY,
 bundle_hash CHAR(64) NULL,
 receipt LONGTEXT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT IGNORE INTO shop_agent_stack_demo_seed(id) VALUES(1);
CREATE TABLE IF NOT EXISTS shop_agent_stack_demo_import (
 id CHAR(36) PRIMARY KEY,
 actor_id BIGINT NOT NULL,
 action VARCHAR(16) NOT NULL,
 bundle_hash CHAR(64) NOT NULL,
 confirmation_hash CHAR(64) NOT NULL,
 expected_state LONGTEXT NOT NULL,
 payload LONGTEXT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'PREVIEW',
 expires_at DATETIME(6) NOT NULL,
 result LONGTEXT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 applied_at DATETIME(6) NULL,
 INDEX idx_demo_import_actor(actor_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
