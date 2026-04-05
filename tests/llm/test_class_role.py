# ---------------------------------------------------------------------------
# Тесты для solid_dashboard.llm.class_role
#
# Покрывает:
#   - classify_class(): все 4 роли (PURE_INTERFACE, INFRA_MODEL, CONFIG, DOMAIN)
#   - _is_pure_interface(): граничные случаи тел методов
#   - _compute_infra_score(): каждый из 5 сигналов по отдельности и в комбинации
#   - import_aliases: разрешение алиасов BaseModel/Base
#   - Граничные случаи: пустое тело класса, namespace-класс, множественное наследование
#
# Запуск:
#   pytest tools/solid_verifier/tests/llm/test_class_role.py -v
# ---------------------------------------------------------------------------

import ast
import textwrap

import pytest

from solid_dashboard.llm.analysis.class_role import (
    ClassRole,
    classify_class,
    _is_pure_interface,
    _compute_infra_score,
)


# ---------------------------------------------------------------------------
# Вспомогательная утилита: парсим исходник и возвращаем первый ClassDef
# ---------------------------------------------------------------------------

def _parse_class(source: str) -> ast.ClassDef:
    """Парсит исходник и возвращает первый ClassDef верхнего уровня."""
    tree = ast.parse(textwrap.dedent(source))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise ValueError("ClassDef не найден в исходнике")


# ---------------------------------------------------------------------------
# TestIsPureInterface — тесты _is_pure_interface()
# ---------------------------------------------------------------------------

class TestIsPureInterface:

    def test_all_abstractmethod_decorators(self):
        """Все методы с @abstractmethod — PURE_INTERFACE."""
        node = _parse_class("""
            from abc import ABC, abstractmethod
            class IFoo(ABC):
                @abstractmethod
                def process(self) -> str: ...
                @abstractmethod
                def validate(self, x: int) -> bool: ...
        """)
        assert _is_pure_interface(node) is True

    def test_ellipsis_body(self):
        """Методы с телом `...` считаются тривиальными."""
        node = _parse_class("""
            class IFoo:
                def process(self) -> str: ...
                def validate(self) -> bool: ...
        """)
        assert _is_pure_interface(node) is True

    def test_pass_body(self):
        """Методы с телом `pass` считаются тривиальными."""
        node = _parse_class("""
            class IFoo:
                def do(self):
                    pass
        """)
        assert _is_pure_interface(node) is True

    def test_raise_not_implemented_body(self):
        """Метод с `raise NotImplementedError` считается тривиальным."""
        node = _parse_class("""
            class IFoo:
                def run(self):
                    raise NotImplementedError
        """)
        assert _is_pure_interface(node) is True

    def test_raise_not_implemented_with_message(self):
        """Метод с `raise NotImplementedError('msg')` — тривиальный."""
        node = _parse_class("""
            class IFoo:
                def run(self):
                    raise NotImplementedError("not implemented")
        """)
        assert _is_pure_interface(node) is True

    def test_docstring_only_body(self):
        """Метод только с docstring считается тривиальным."""
        node = _parse_class("""
            class IFoo:
                def run(self):
                    \"\"\"Does nothing yet.\"\"\"
        """)
        assert _is_pure_interface(node) is True

    def test_real_logic_returns_false(self):
        """Метод с реальной логикой — НЕ PURE_INTERFACE."""
        node = _parse_class("""
            class Base:
                def __init__(self):
                    self.x = 1
                def process(self) -> str:
                    return str(self.x)
        """)
        assert _is_pure_interface(node) is False

    def test_mixed_abstract_and_real_returns_false(self):
        """Один реальный метод среди абстрактных — НЕ PURE_INTERFACE."""
        node = _parse_class("""
            from abc import abstractmethod
            class MixedBase:
                @abstractmethod
                def process(self) -> str: ...
                def helper(self) -> int:
                    return 42
        """)
        assert _is_pure_interface(node) is False

    def test_empty_class_body_returns_false(self):
        """Класс без методов (только pass) — НЕ PURE_INTERFACE."""
        node = _parse_class("""
            class Empty:
                pass
        """)
        assert _is_pure_interface(node) is False

    def test_only_class_variable_returns_false(self):
        """Класс только с атрибутами класса — НЕ PURE_INTERFACE."""
        node = _parse_class("""
            class Constants:
                MAX = 100
                MIN = 0
        """)
        assert _is_pure_interface(node) is False


# ---------------------------------------------------------------------------
# TestComputeInfraScore — тесты _compute_infra_score()
# ---------------------------------------------------------------------------

class TestComputeInfraScore:

    def test_score_known_base_gives_two_points(self):
        """Прямое наследование от BaseModel даёт +2 (самый сильный сигнал)."""
        node = _parse_class("""
            class UserSchema(BaseModel):
                name: str
                age: int
        """)
        bases = ["BaseModel"]
        assert _compute_infra_score(node, bases) >= 2

    def test_score_tablename_gives_one_point(self):
        """__tablename__ даёт +1 балл (SQLAlchemy ORM)."""
        node = _parse_class("""
            class User:
                __tablename__ = 'users'
                id: int
        """)
        score = _compute_infra_score(node, [])
        assert score >= 1

    def test_score_model_config_gives_one_point(self):
        """model_config даёт +1 балл (Pydantic v2)."""
        node = _parse_class("""
            class Item:
                model_config = {}
                price: float
        """)
        score = _compute_infra_score(node, [])
        assert score >= 1

    def test_score_ann_assign_ratio_gives_one_point(self):
        """Более 70% тела — AnnAssign даёт +1 балл."""
        node = _parse_class("""
            class DataBag:
                a: int
                b: str
                c: float
                d: bool
        """)
        score = _compute_infra_score(node, [])
        assert score >= 1

    def test_score_orm_field_calls_gives_one_point(self):
        """Вызов Column() в теле даёт +1 балл."""
        node = _parse_class("""
            class Product:
                id = Column(Integer, primary_key=True)
                name = Column(String)
        """)
        score = _compute_infra_score(node, [])
        assert score >= 1

    def test_score_domain_class_zero(self):
        """Обычный доменный класс набирает 0 баллов."""
        node = _parse_class("""
            class OrderService:
                def __init__(self, repo):
                    self._repo = repo
                def create(self, data):
                    return self._repo.save(data)
        """)
        score = _compute_infra_score(node, [])
        assert score == 0


# ---------------------------------------------------------------------------
# TestClassifyClassPureInterface — classify_class() -> PURE_INTERFACE
# ---------------------------------------------------------------------------

class TestClassifyClassPureInterface:

    def test_abc_all_abstract(self):
        """ABC-класс со всеми @abstractmethod -> PURE_INTERFACE."""
        node = _parse_class("""
            from abc import ABC, abstractmethod
            class IRepository(ABC):
                @abstractmethod
                def get(self, id: int): ...
                @abstractmethod
                def save(self, obj): ...
        """)
        assert classify_class(node) == ClassRole.PURE_INTERFACE

    def test_no_abc_base_but_abstract_bodies(self):
        """Без явного ABC, но все методы с pass — PURE_INTERFACE."""
        node = _parse_class("""
            class IHandler:
                def handle(self, event): pass
                def rollback(self): pass
        """)
        assert classify_class(node) == ClassRole.PURE_INTERFACE

    def test_abc_with_real_init_is_domain(self):
        """ABC с реальным __init__ — это НЕ PURE_INTERFACE, а DOMAIN."""
        node = _parse_class("""
            from abc import ABC, abstractmethod
            class BaseHandler(ABC):
                def __init__(self, name: str):
                    self.name = name
                @abstractmethod
                def handle(self): ...
        """)
        assert classify_class(node) != ClassRole.PURE_INTERFACE


# ---------------------------------------------------------------------------
# TestClassifyClassConfig — classify_class() -> CONFIG
# ---------------------------------------------------------------------------

class TestClassifyClassConfig:

    def test_base_settings(self):
        """Класс, наследующий BaseSettings -> CONFIG."""
        node = _parse_class("""
            class AppSettings(BaseSettings):
                db_url: str = 'sqlite:///db.sqlite3'
                debug: bool = False
        """)
        assert classify_class(node) == ClassRole.CONFIG

    def test_settings_base(self):
        """Класс, наследующий Settings -> CONFIG."""
        node = _parse_class("""
            class ProductionSettings(Settings):
                debug: bool = False
        """)
        assert classify_class(node) == ClassRole.CONFIG

    def test_base_config(self):
        """Класс, наследующий BaseConfig -> CONFIG."""
        node = _parse_class("""
            class DatabaseConfig(BaseConfig):
                host: str = 'localhost'
                port: int = 5432
        """)
        assert classify_class(node) == ClassRole.CONFIG


# ---------------------------------------------------------------------------
# TestClassifyClassInfraModel — classify_class() -> INFRA_MODEL
# ---------------------------------------------------------------------------

class TestClassifyClassInfraModel:

    def test_pydantic_base_model(self):
        """Класс на BaseModel -> INFRA_MODEL."""
        node = _parse_class("""
            class UserSchema(BaseModel):
                name: str
                email: str
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL

    def test_sqlalchemy_orm(self):
        """SQLAlchemy-модель с __tablename__ -> INFRA_MODEL."""
        node = _parse_class("""
            class User(Base):
                __tablename__ = 'users'
                id = Column(Integer, primary_key=True)
                name = Column(String)
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL

    def test_declarative_base(self):
        """Класс на DeclarativeBase -> INFRA_MODEL."""
        node = _parse_class("""
            class Article(DeclarativeBase):
                __tablename__ = 'articles'
                title: str
                body: str
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL


# ---------------------------------------------------------------------------
# TestClassifyClassDomain — classify_class() -> DOMAIN
# ---------------------------------------------------------------------------

class TestClassifyClassDomain:

    def test_plain_service(self):
        """Обычный сервисный класс без наследования -> DOMAIN."""
        node = _parse_class("""
            class OrderService:
                def __init__(self, repo):
                    self._repo = repo
                def create(self, data: dict):
                    return self._repo.save(data)
        """)
        assert classify_class(node) == ClassRole.DOMAIN

    def test_subclass_with_logic(self):
        """Подкласс с реальной логикой -> DOMAIN."""
        node = _parse_class("""
            class ConcreteHandler(BaseHandler):
                def __init__(self):
                    super().__init__()
                def handle(self, event):
                    return event.process()
        """)
        assert classify_class(node) == ClassRole.DOMAIN

    def test_class_no_parents(self):
        """Класс без наследования с реальными методами -> DOMAIN."""
        node = _parse_class("""
            class Calculator:
                def add(self, a: int, b: int) -> int:
                    return a + b
                def subtract(self, a: int, b: int) -> int:
                    return a - b
        """)
        assert classify_class(node) == ClassRole.DOMAIN


# ---------------------------------------------------------------------------
# TestImportAliases — разрешение алиасов импортов
# ---------------------------------------------------------------------------

class TestImportAliases:

    def test_alias_base_model_as_bm(self):
        """from pydantic import BaseModel as BM — через алиас должно быть INFRA_MODEL."""
        node = _parse_class("""
            class UserOut(BM):
                id: int
                name: str
                email: str
        """)
        # Без алиаса — DOMAIN (BM неизвестен)
        assert classify_class(node, import_aliases={}) == ClassRole.DOMAIN
        # С алиасом — INFRA_MODEL
        assert classify_class(node, import_aliases={"BM": "BaseModel"}) == ClassRole.INFRA_MODEL

    def test_alias_declarative_base(self):
        """from sqlalchemy.orm import DeclarativeBase as DB — алиас разрешается."""
        node = _parse_class("""
            class Product(DB):
                __tablename__ = 'products'
                id = Column(Integer, primary_key=True)
        """)
        assert classify_class(node, import_aliases={"DB": "DeclarativeBase"}) == ClassRole.INFRA_MODEL

    def test_no_aliases_unknown_base_is_domain(self):
        """Неизвестная база без алиасов -> DOMAIN."""
        node = _parse_class("""
            class Foo(UnknownBase):
                def do(self):
                    return 42
        """)
        assert classify_class(node, import_aliases=None) == ClassRole.DOMAIN

    def test_alias_base_settings_as_cfg(self):
        """from pydantic_settings import BaseSettings as Cfg — CONFIG через алиас."""
        node = _parse_class("""
            class AppCfg(Cfg):
                debug: bool = False
        """)
        assert classify_class(node, import_aliases={"Cfg": "BaseSettings"}) == ClassRole.CONFIG


# ---------------------------------------------------------------------------
# TestClassifyClassEdgeCases — граничные случаи
# ---------------------------------------------------------------------------

class TestClassifyClassEdgeCases:

    def test_multiple_inheritance_one_infra(self):
        """Одна из баз — BaseModel: INFRA_MODEL даже при множественном наследовании."""
        node = _parse_class("""
            class HybridSchema(SomeMixin, BaseModel):
                value: int
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL

    def test_config_takes_priority_over_infra(self):
        """BaseSettings входит и в CONFIG, и в INFRA_BASES — должен вернуться CONFIG."""
        node = _parse_class("""
            class AppConfig(BaseSettings):
                host: str = 'localhost'
        """)
        # CONFIG имеет приоритет над INFRA_MODEL
        role = classify_class(node)
        assert role == ClassRole.CONFIG

    def test_empty_body_class_is_domain(self):
        """Класс с только pass и без наследования -> DOMAIN."""
        node = _parse_class("""
            class Stub:
                pass
        """)
        assert classify_class(node) == ClassRole.DOMAIN

    def test_pure_interface_takes_priority_over_domain(self):
        """PURE_INTERFACE проверяется первым — до CONFIG и INFRA_MODEL."""
        node = _parse_class("""
            from abc import ABC, abstractmethod
            class IFoo(ABC):
                @abstractmethod
                def run(self): ...
        """)
        assert classify_class(node) == ClassRole.PURE_INTERFACE

    def test_import_aliases_none_does_not_crash(self):
        """import_aliases=None не вызывает ошибки (fallback к пустому dict)."""
        node = _parse_class("""
            class SimpleClass:
                def method(self):
                    return 1
        """)
        # Не должно бросить исключение
        role = classify_class(node, import_aliases=None)
        assert role == ClassRole.DOMAIN
