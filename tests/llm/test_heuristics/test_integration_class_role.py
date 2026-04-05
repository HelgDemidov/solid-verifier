"""
Интеграционные тесты: классификатор ролей + логика фильтрации эвристик.

Проверяет три реальных граничных случая, обнаруженных при анализе
работы эвристик LSP и OCP на реальных кодовых базах:

  Кейс A (LSP-H-004): ABC-интерфейс без собственного __init__ давал
    false positive — легитимная конкретизация сигнатуры в подклассе.

  Кейс B (OCP / INFRA_MODEL): Pydantic BaseModel и SQLAlchemy Base
    создавали шум в кандидатах OCP; INFRA_MODEL должен исключаться.

  Кейс C (OCP / CONFIG): Settings(BaseSettings) — конфигурационный
    класс, не подходит для SOLID-анализа.

Запуск:
  pytest tools/solid_verifier/tests/llm/test_heuristics/test_integration_class_role.py -v
"""
import ast
import textwrap

import pytest

from solid_dashboard.llm.analysis.class_role import ClassRole, classify_class

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _first_class(source: str) -> ast.ClassDef:
    """Парсит исходник и возвращает первый ClassDef верхнего уровня."""
    tree = ast.parse(textwrap.dedent(source))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise ValueError("ClassDef не найден в исходнике")


def _should_skip_for_solid(
    class_node: ast.ClassDef,
    import_aliases: dict[str, str] | None = None,
) -> bool:
    """
    Имитирует логику фильтрации на входе эвристик LSP/OCP.
    Возвращает True, если класс должен быть исключен из SOLID-анализа
    (не является DOMAIN-классом).
    """
    role = classify_class(class_node, import_aliases=import_aliases)
    return role != ClassRole.DOMAIN


# ---------------------------------------------------------------------------
# TestHeuristicsClassRoleIntegration
# ---------------------------------------------------------------------------

class TestHeuristicsClassRoleIntegration:

    # -----------------------------------------------------------------------
    # Кейс A: LSP-H-004 — ABC-интерфейс без __init__
    # -----------------------------------------------------------------------

    def test_lsp_h004_abc_without_init_is_pure_interface(self):
        """
        Кейс A-1: ABC с только @abstractmethod и без __init__
        должен получать роль PURE_INTERFACE, а не DOMAIN.
        LSP-эвристика должна пропускать таких кандидатов.
        """
        node = _first_class("""
            from abc import ABC, abstractmethod
            class IPaymentGateway(ABC):
                @abstractmethod
                def charge(self, amount: float) -> bool: ...
                @abstractmethod
                def refund(self, transaction_id: str) -> bool: ...
        """)
        assert classify_class(node) == ClassRole.PURE_INTERFACE
        assert _should_skip_for_solid(node) is True

    def test_lsp_h004_abc_with_docstrings_only_is_pure_interface(self):
        """
        Кейс A-2: ABC-методы с только docstring (без pass / ... / raise)
        тоже считаются тривиальными — PURE_INTERFACE.
        """
        node = _first_class("""
            from abc import ABC, abstractmethod
            class INotifier(ABC):
                @abstractmethod
                def send(self, message: str) -> None:
                    \"\"\"Отправляет уведомление получателю.\"\"\"
                @abstractmethod
                def is_connected(self) -> bool:
                    \"\"\"Возвращает статус соединения.\"\"\"
        """)
        assert classify_class(node) == ClassRole.PURE_INTERFACE
        assert _should_skip_for_solid(node) is True

    def test_lsp_h004_abc_with_real_init_is_domain(self):
        """
        Кейс A-3: ABC с реальным __init__ — конкретный базовый класс,
        НЕ чистый интерфейс. LSP-эвристика должна его анализировать.
        """
        node = _first_class("""
            from abc import ABC, abstractmethod
            class BaseValidator(ABC):
                def __init__(self, strict: bool = False):
                    self.strict = strict
                @abstractmethod
                def validate(self, data: dict) -> bool: ...
        """)
        assert classify_class(node) != ClassRole.PURE_INTERFACE
        assert classify_class(node) == ClassRole.DOMAIN
        assert _should_skip_for_solid(node) is False

    def test_lsp_h004_concrete_subclass_without_override_is_domain(self):
        """
        Кейс A-4: Конкретный подкласс ABC-интерфейса — DOMAIN.
        LSP-эвристика должна проверять именно таких кандидатов.
        """
        node = _first_class("""
            class StripeGateway(IPaymentGateway):
                def __init__(self, api_key: str):
                    self.api_key = api_key
                def charge(self, amount: float) -> bool:
                    return True
                def refund(self, transaction_id: str) -> bool:
                    return True
        """)
        assert classify_class(node) == ClassRole.DOMAIN
        assert _should_skip_for_solid(node) is False

    # -----------------------------------------------------------------------
    # Кейс B: OCP — Pydantic BaseModel и SQLAlchemy Base как шум
    # -----------------------------------------------------------------------

    def test_ocp_pydantic_base_model_is_infra_not_domain(self):
        """
        Кейс B-1: Pydantic BaseModel — типичный источник шума в кандидатах OCP.
        Должен получать роль INFRA_MODEL и исключаться из SOLID-анализа.
        """
        node = _first_class("""
            class OrderCreateSchema(BaseModel):
                product_id: int
                quantity: int
                discount: float = 0.0
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL
        assert _should_skip_for_solid(node) is True

    def test_ocp_pydantic_base_model_via_alias_is_infra(self):
        """
        Кейс B-2: Pydantic BaseModel через алиас (from pydantic import BaseModel as BM).
        Без алиаса — DOMAIN; с алиасом — INFRA_MODEL.
        """
        node = _first_class("""
            class ProductResponseSchema(BM):
                id: int
                name: str
                price: float
                in_stock: bool
        """)
        assert classify_class(node, import_aliases={}) == ClassRole.DOMAIN
        assert classify_class(node, import_aliases={"BM": "BaseModel"}) == ClassRole.INFRA_MODEL
        assert _should_skip_for_solid(node, {"BM": "BaseModel"}) is True

    def test_ocp_sqlalchemy_orm_via_tablename_is_infra(self):
        """
        Кейс B-3: SQLAlchemy ORM через Base (не в KNOWN_INFRA_BASES).
        Детектируется через InfraScore: __tablename__ (+1) + Column() (+1) >= 2.
        """
        node = _first_class("""
            class Invoice(Base):
                __tablename__ = 'invoices'
                id = Column(Integer, primary_key=True)
                amount = Column(Numeric(10, 2))
                paid = Column(Boolean, default=False)
        """)
        assert classify_class(node) == ClassRole.INFRA_MODEL
        assert _should_skip_for_solid(node) is True

    def test_ocp_domain_class_with_many_fields_is_still_domain(self):
        """
        Кейс B-4: Доменный класс с большим количеством аннотированных атрибутов
        НЕ должен ошибочно попадать в INFRA_MODEL.
        Высокий AnnAssign-ratio сам по себе не даёт INFRA_MODEL (порог < 2).
        """
        node = _first_class("""
            class ReportConfig:
                title: str
                author: str
                date: str
                include_charts: bool
                max_rows: int
        """)
        assert classify_class(node) == ClassRole.DOMAIN
        assert _should_skip_for_solid(node) is False

    # -----------------------------------------------------------------------
    # Кейс C: Settings(BaseSettings) — конфигурационные классы
    # -----------------------------------------------------------------------

    def test_ocp_base_settings_is_config_not_domain(self):
        """
        Кейс C-1: Прямой наследник BaseSettings получает роль CONFIG
        и должен исключаться из SOLID-анализа OCP/LSP.
        """
        node = _first_class("""
            class AppSettings(BaseSettings):
                database_url: str
                redis_url: str
                debug: bool = False
                secret_key: str = ''
        """)
        assert classify_class(node) == ClassRole.CONFIG
        assert _should_skip_for_solid(node) is True

    def test_ocp_settings_subclass_chain_is_config(self):
        """
        Кейс C-2: Подкласс Settings (BaseSettings → Settings).
        Прямое наследование от Settings также должно давать CONFIG.
        """
        node = _first_class("""
            class ProductionSettings(Settings):
                debug: bool = False
                allowed_hosts: list = []
        """)
        assert classify_class(node) == ClassRole.CONFIG
        assert _should_skip_for_solid(node) is True

    def test_ocp_base_config_is_config(self):
        """
        Кейс C-3: Pydantic BaseConfig / собственный BaseConfig проекта.
        Оба паттерна должны давать CONFIG и исключаться из анализа.
        """
        node = _first_class("""
            class DatabaseConfig(BaseConfig):
                host: str = 'localhost'
                port: int = 5432
                name: str = 'mydb'
        """)
        assert classify_class(node) == ClassRole.CONFIG
        assert _should_skip_for_solid(node) is True

    def test_ocp_config_class_name_without_config_base_is_domain(self):
        """
        Кейс C-4: Класс с 'Config' в имени, но без Config-базы —
        это НЕ конфигурационный класс. Классификация идёт по базам,
        а не по имени класса.
        """
        node = _first_class("""
            class ReportConfig:
                def __init__(self, output_format: str):
                    self.output_format = output_format
                def to_dict(self) -> dict:
                    return {'format': self.output_format}
        """)
        assert classify_class(node) == ClassRole.DOMAIN
        assert _should_skip_for_solid(node) is False
