"""
Модели базы данных
"""

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    """Модель пользователя бота"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vk_id = Column(BigInteger, unique=True, nullable=False, index=True)
    first_name = Column(String(100), nullable=False)
    grade = Column(Integer, nullable=True)
    solved_tasks = Column(Text, default="")
    created_at = Column(TIMESTAMP, server_default=func.now())

    answer_history = relationship(
        "AnswerHistory", back_populates="user", cascade="all, delete-orphan"
    )


class Task(Base):
    """Модель задания по физике"""

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_number = Column(Integer, nullable=False, index=True)
    answer = Column(Text, nullable=False)
    image_attachment = Column(String(255), nullable=True)

    answer_history = relationship(
        "AnswerHistory", back_populates="task", cascade="all, delete-orphan"
    )


class AnswerHistory(Base):
    """Модель истории ответов для детальной аналитики"""

    __tablename__ = "answer_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger,
        ForeignKey("users.vk_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_correct = Column(Boolean, nullable=False)
    answered_at = Column(TIMESTAMP, server_default=func.now())

    user = relationship("User", back_populates="answer_history")
    task = relationship("Task", back_populates="answer_history")


class UserStateModel(Base):
    """Хранилище FSM-состояний в БД, чтобы не терять их при перезапуске контейнера"""

    __tablename__ = "user_fsm_states"

    vk_id = Column(BigInteger, primary_key=True, index=True)
    state = Column(String(100), nullable=True)
    data_json = Column(Text, nullable=True)