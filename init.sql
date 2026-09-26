-- Схема PostgreSQL для VK-бота по физике.
-- task_number — категория 1..20; конкретный вариант определяется tasks.id.
-- Всего поддерживается 20 категорий по 30 задач (600 вариантов).

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    vk_id BIGINT NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL DEFAULT 'Ученик',
    grade SMALLINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_users_grade CHECK (grade IS NULL OR grade IN (10, 11))
);

CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY,
    task_number SMALLINT NOT NULL,
    answer TEXT NOT NULL,
    image_attachment VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT ck_tasks_number CHECK (task_number BETWEEN 1 AND 20),
    CONSTRAINT ck_tasks_answer_not_empty CHECK (btrim(answer) <> '')
);

CREATE TABLE IF NOT EXISTS answer_history (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    is_correct BOOLEAN NOT NULL,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_solved_tasks (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    solved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, task_id)
);

CREATE TABLE IF NOT EXISTS user_fsm_states (
    vk_id BIGINT PRIMARY KEY,
    state VARCHAR(100),
    data_json TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_users_vk_id ON users(vk_id);
CREATE INDEX IF NOT EXISTS ix_tasks_task_number_id ON tasks(task_number, id);
CREATE INDEX IF NOT EXISTS ix_answer_history_user_date ON answer_history(user_id, answered_at DESC);
CREATE INDEX IF NOT EXISTS ix_answer_history_task ON answer_history(task_id);
CREATE INDEX IF NOT EXISTS ix_answer_history_answered_at ON answer_history(answered_at);