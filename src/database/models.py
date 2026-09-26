"""SQLAlchemy models for the bot database."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()
SQLITE_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id = Column(SQLITE_BIGINT_PK, primary_key=True, autoincrement=True)
    vk_id = Column(BigInteger, unique=True, nullable=False, index=True)
    first_name = Column(String(100), nullable=False, default="Ученик")
    grade = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    answer_history = relationship("AnswerHistory", back_populates="user", cascade="all, delete-orphan")
    solved_tasks = relationship("UserSolvedTask", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (CheckConstraint("grade IS NULL OR grade IN (10, 11)", name="ck_users_grade"),)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(SQLITE_BIGINT_PK, primary_key=True, autoincrement=True)
    task_number = Column(Integer, nullable=False, index=True)
    answer = Column(Text, nullable=False)
    image_attachment = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    answer_history = relationship("AnswerHistory", back_populates="task", cascade="all, delete-orphan")
    solved_by = relationship("UserSolvedTask", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("task_number BETWEEN 1 AND 20", name="ck_tasks_number"),
        CheckConstraint("trim(answer) <> ''", name="ck_tasks_answer_not_empty"),
    )


class AnswerHistory(Base):
    __tablename__ = "answer_history"

    id = Column(SQLITE_BIGINT_PK, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    is_correct = Column(Boolean, nullable=False)
    answered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    user = relationship("User", back_populates="answer_history")
    task = relationship("Task", back_populates="answer_history")


class UserSolvedTask(Base):
    __tablename__ = "user_solved_tasks"

    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    task_id = Column(BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    solved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="solved_tasks")
    task = relationship("Task", back_populates="solved_by")


class UserStateModel(Base):
    __tablename__ = "user_fsm_states"

    vk_id = Column(BigInteger, primary_key=True, index=True)
    state = Column(String(100), nullable=True)
    data_json = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
