-- Инициализация базы данных для ВК Бота по Физике

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Таблица заданий
CREATE TABLE IF NOT EXISTS tasks (
  id               SERIAL PRIMARY KEY,
  task_number      INT NOT NULL,              -- 1–20
  answer           TEXT NOT NULL,             -- Правильный ответ
  image_attachment TEXT DEFAULT NULL          -- Строка вложения фото ВК (напр. photo-123_456)
);

CREATE INDEX IF NOT EXISTS idx_task_number ON tasks(task_number);

-- 2. Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL PRIMARY KEY,
  vk_id         BIGINT UNIQUE NOT NULL,    -- ID пользователя ВКонтакте
  first_name    TEXT NOT NULL,             -- Имя пользователя
  grade         INT DEFAULT NULL,          -- Класс (10 или 11)
  solved_tasks  TEXT DEFAULT '',           -- Список ID решенных задач через запятую
  created_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vk_id ON users(vk_id);

-- 3. История ответов
CREATE TABLE IF NOT EXISTS answer_history (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT REFERENCES users(vk_id) ON DELETE CASCADE,
  task_id       INT REFERENCES tasks(id) ON DELETE CASCADE,
  is_correct    BOOLEAN NOT NULL,
  answered_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_answer_user ON answer_history(user_id);
CREATE INDEX IF NOT EXISTS idx_answer_date ON answer_history(answered_at);