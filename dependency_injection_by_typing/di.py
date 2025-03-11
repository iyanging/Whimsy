#  Copyright (c) 2025 iyanging
#
#  Whimsy is licensed under Mulan PSL v2.
#  You can use this software according to the terms and conditions of the Mulan PSL v2.
#  You may obtain a copy of Mulan PSL v2 at:
#      http://license.coscl.org.cn/MulanPSL2
#
#  THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
#  EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
#  MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
#
#  See the Mulan PSL v2 for more details.
#

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, NoReturn, Self, TypeIs, cast, overload

__all__ = [
    "DependencyOption",
    "DependencyRegistry",
    "managed",
]


import logging
import types
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import MISSING, dataclass, fields, is_dataclass
from importlib import import_module
from inspect import Parameter, getmembers, signature
from pkgutil import walk_packages

_ProviderType = Literal["singleton", "factory"]


@overload
def managed[T](
    ctor: type[T],
    *,
    as_: _ProviderType = "singleton",
) -> type[T]: ...


@overload
def managed[**P](
    ctor: Callable[P, Sequence[Any] | Set[Any] | Mapping[Any, Any]],
    *,
    as_: _ProviderType = "singleton",
) -> NoReturn:
    """禁止将常见的容器 (list / tuple / set / dict) 作为依赖."""
    # 此 overload 需要早于下一个 managed() 声明,
    # 因为 overload 匹配选择是按声明顺序找到第1个匹配项


@overload
def managed[**P, R](
    ctor: Callable[P, R],
    *,
    as_: _ProviderType = "singleton",
) -> Callable[P, R]: ...


@overload
def managed(
    ctor: None = None,
    *,
    as_: _ProviderType = "singleton",
) -> _ManagedWrapper: ...


def managed[T, **P, R](
    ctor: type[T] | Callable[P, R] | None = None,
    *,
    as_: _ProviderType = "singleton",
) -> type[T] | Callable[P, R] | _ManagedWrapper:
    if isinstance(ctor, type):  # 因为 type[T] 也是 callable, 所以先判断是否为 type
        return _ManagedWrapper(provider_type=as_)(cast(type[T], ctor))

    elif callable(ctor):
        return _ManagedWrapper(provider_type=as_)(ctor)

    else:
        return _ManagedWrapper(provider_type=as_)


@dataclass(kw_only=True, repr=True)
class DependencyOption:
    provider_type: _ProviderType


@dataclass(kw_only=True)
class _ManagedWrapper:
    """Provide precise __call__() overload definition."""

    _DEPENDENCY_OPTION_KEY: ClassVar[Final[str]] = "__dependency_option__"

    provider_type: Final[_ProviderType]

    @overload
    def __call__[T](self, ctor: type[T]) -> type[T]: ...

    @overload
    def __call__[**P, R](
        self,
        ctor: Callable[P, R],
    ) -> Callable[P, R]: ...

    def __call__[T, **P, R](
        self,
        ctor: type[T] | Callable[P, R],
    ) -> type[T] | Callable[P, R]:
        dependency_option = DependencyOption(provider_type=self.provider_type)
        setattr(ctor, self._DEPENDENCY_OPTION_KEY, dependency_option)
        return ctor

    @classmethod
    def is_managed(
        cls,
        what: Any,
    ) -> bool:
        return hasattr(what, cls._DEPENDENCY_OPTION_KEY)

    @classmethod
    def get_dependency_option(
        cls,
        ctor: Callable[..., Any],
    ) -> DependencyOption:
        return getattr(ctor, cls._DEPENDENCY_OPTION_KEY)


# ***** Provider Hierarchy *****
# *
# ** Provider
# ** |
# ** |- ConstructableProvider
# ** |  |
# ** |  |- SingletonProvider
# ** |  |
# ** |  |- FactoryProvider
# ** |
# ** |- ObjectProvider
# ** |
# ** |- ListProvider


class _Provider[T](ABC):
    """Abstraction of `call to provide`.

    Subclasses must handle initialization themselves.
    """

    @abstractmethod
    def __call__(self) -> T:
        raise NotImplementedError

    @abstractmethod
    def __repr__(self) -> str:
        raise NotImplementedError


class _ConstructableProvider[T](_Provider[T], ABC):
    """Abstraction of `must init then call to provide`."""

    @abstractmethod
    def __init__(
        self,
        ctor: Callable[..., T],
        *args: _Provider[Any],
        **kwargs: _Provider[Any],
    ) -> None:
        raise NotImplementedError


class _SingletonProvider[T](_ConstructableProvider[T]):
    _factory: _FactoryProvider[T]
    _instance: T | None

    def __init__(
        self,
        ctor: Callable[..., T],
        *args: _Provider[Any],
        **kwargs: _Provider[Any],
    ) -> None:
        self._factory = _FactoryProvider(ctor, *args, **kwargs)
        self._instance = None

    def __call__(self) -> T:
        if self._instance is not None:
            return self._instance

        else:
            self._instance = self._factory()

            return self._instance

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__qualname__}(_factory={self._factory}, _instance={self._instance})"
        )


class _FactoryProvider[T](_ConstructableProvider[T]):
    _ctor: Callable[..., T]
    _args: tuple[_Provider[Any], ...]
    _kwargs: Mapping[str, _Provider[Any]]

    def __init__(
        self,
        ctor: Callable[..., T],
        *args: _Provider[Any],
        **kwargs: _Provider[Any],
    ) -> None:
        self._ctor = ctor
        self._args = args
        self._kwargs = kwargs

    def __call__(self) -> T:
        # use tuple() rather than generator to evaluate args before kwargs,
        # so we can simulate the normal function invocation
        args = tuple(arg() for arg in self._args)
        kwargs = {kw: arg() for kw, arg in self._kwargs.items()}

        return self._ctor(*args, **kwargs)

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}({self._ctor}, *, **)"


@dataclass(repr=True)
class _ObjectCtor:
    _val: Any

    def __hash__(self) -> int:
        return hash(self._val)

    def __call__(self) -> NoReturn:
        raise Exception(f"instance of `{self}` should not be __call__()")


class _ObjectProvider[T](_Provider[T]):
    _instance: T

    def __init__(self, obj: T) -> None:
        self._instance = obj

    def __call__(self) -> T:
        return self._instance

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(_instance={self._instance})"


class _ListProvider[T](_Provider[list[T]]):
    _elements: list[_Provider[T]]

    def __init__(self, elements: list[_Provider[T]]) -> None:
        self._elements = [*elements]  # shallow copy

    def __call__(self) -> list[T]:
        # create a new list each time
        return [e() for e in self._elements]

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}({self._elements})"


# ****************************
# ******* The registry *******
# ****************************

type _DependencyCtor = Callable[..., _InstanceOfMonadPrimitiveMetaTypes]


@dataclass
class _DependencyCtorContext:
    option: DependencyOption
    provider: _Provider[_InstanceOfMonadPrimitiveMetaTypes] | None


class DependencyRegistry:
    # The reason why not combine these status into one is that:
    #   * keep the semantic: "one ctor in diff proto has the same provider"
    #   * provider cannot be eagerly built, due to the possible absence of deps
    _proto_to_ctor_set: defaultdict[
        _MonadPrimitiveMetaTypes,
        set[_DependencyCtor],
    ]
    _ctor_to_ctx: dict[_DependencyCtor, _DependencyCtorContext]

    def __init__(self) -> None:
        self._proto_to_ctor_set = defaultdict(set)
        self._ctor_to_ctx = {}

        self.register_val(None)
        self.register_val(self)

    def get_dependency[T](self, dep_type: type[T]) -> T:
        dep_rt = _lint_type_to_run_type(dep_type)

        providers: list[_Provider[_InstanceOfMonadPrimitiveMetaTypes]] | None = None
        for possible_dep_rt in _unpack_if_union(dep_rt):
            providers = self._get_providers_from_container(possible_dep_rt)
            if providers is not None:
                break

        if providers is None:
            raise DependencyNotFoundError(dep_rt)

        if len(providers) != 1:
            raise NoUniqueDependencyError(dep_type)

        provider = _first(providers)
        logging.debug("Found %s", provider)

        dep = provider()

        logging.debug("Return %s (id: %s)", type(dep), id(dep))

        return _run_type_val_to_lint_type_val(dep, dep_type)

    def get_dependencies[T](self, dep_type: type[T]) -> list[T]:
        dep_rt = _lint_type_to_run_type(dep_type)

        providers: list[_Provider[_InstanceOfMonadPrimitiveMetaTypes]] = []
        for possible_dep_rt in _unpack_if_union(dep_rt):
            providers.extend(self._get_providers_from_container(possible_dep_rt) or [])

        if not providers:
            raise DependencyNotFoundError(dep_rt)

        provider = _ListProvider(providers)
        logging.debug("Built an temporarily %s", provider)

        dep = provider()

        logging.debug("Return %s (id: %s)", type(dep), id(dep))

        return _run_type_val_to_lint_type_val(dep, dep_type)

    def scan(
        self,
        modules: collections.abc.Iterable[types.ModuleType],
    ) -> Self:
        dep_def_list = self._gather_dep_def_from_modules(modules)

        for ctor, option in dep_def_list:
            self.register_ctor(ctor, option)

        return self

    @overload
    def register_ctor(
        self,
        ctor: Callable[..., None],
        option: DependencyOption,
    ) -> NoReturn: ...

    @overload
    def register_ctor(
        self,
        ctor: Callable[..., Any],
        option: DependencyOption,
    ) -> Self: ...

    def register_ctor(
        self,
        ctor: Callable[..., Any],
        option: DependencyOption,
    ) -> Self:
        prototypes = self._get_prototypes_by_ctor(ctor)

        for proto in prototypes:
            ctor_set = self._proto_to_ctor_set[proto]

            if ctor not in ctor_set:
                ctor_set.add(ctor)
                self._ctor_to_ctx[ctor] = _DependencyCtorContext(
                    option=option,
                    provider=None,
                )

            else:
                raise ConstructorExistsError(ctor)

        return self

    def register_val(self, v: object) -> Self:
        v = cast(_object, v)

        ctor = _ObjectCtor(v)
        prototypes = self._get_prototypes_by_val(v)

        for proto in prototypes:
            ctor_set = self._proto_to_ctor_set[proto]

            if ctor not in ctor_set:
                ctor_set.add(ctor)
                self._ctor_to_ctx[ctor] = _DependencyCtorContext(
                    option=DependencyOption(provider_type="singleton"),
                    provider=_ObjectProvider(v),
                )

            else:
                raise ConstructorExistsError(ctor)

        return self

    def _get_providers_from_container[T: _InstanceOfMonadPrimitiveMetaTypes](
        self,
        dep_type: type[T],
    ) -> list[_Provider[T]] | None:
        provider_key = self._calc_provider_key(dep_type)
        return self._container.get(provider_key, None)

    @overload
    def _get_or_make_provider_from_registry(
        self,
        dep_type: Any,
        *,
        _require_unique: typing.Literal[True] = True,
    ) -> _Provider[Any]: ...

    @overload
    def _get_or_make_provider_from_registry(
        self,
        dep_type: Any,
        *,
        _require_unique: typing.Literal[False] = False,
    ) -> list[_Provider[Any]]: ...

    def _get_or_make_provider_from_registry(
        self,
        dep_type: Any,
        *,
        _require_unique: bool = True,
    ) -> _Provider[Any] | list[_Provider[Any]]:
        # Eg:
        # `Annotated[list[Service], 1]`: Annotated + (list[Service], 1)
        # `Annotated[int, 1]`: Annotated + (int, 1)
        # `Annotated[Annotated[int, 1], 2]`: Annotated + (int, 1, 2)
        if isinstance(dep_type, _MetaTypingAnnotatedAlias):
            assert typing.get_origin(dep_type) is typing.Annotated

            unannotated = typing.get_args(dep_type)[0]  # such as `list[Service]`, `int`
            return self._get_or_make_provider_from_registry(unannotated)

        # Eg: `List[Service]`, `typing.Iterable[Service]`, `App[Service]`
        elif isinstance(dep_type, _MetaTypingGenericAlias):
            origin_type = typing.get_origin(dep_type)

            # `List[Service]`: list + (Service,)  # same as `list[Service]`
            # `typing.Iterable[Service]`: collections.abc.Iterable + (Service,)
            # `collections.abc.Iterable[Service]`: collections.abc.Iterable + (Service,)
            if isinstance(origin_type, type) and issubclass(
                origin_type, collections.abc.Collection
            ):
                if issubclass(origin_type, Sequence) or origin_type is collections.abc.Iterable:
                    real_dep_type = typing.get_args(dep_type)[0]  # such as `Service`
                    return _ListProvider(
                        self._get_or_make_provider_from_registry(
                            real_dep_type,
                            _require_unique=False,
                        )
                    )

                else:  # such as `dict[str, Service]`
                    raise UnsupportedContainerTypeError(dep_type)

            # `App[Service]`
            else:
                pass

        # Eg: `list[Service]`
        elif isinstance(dep_type, _MetaTypesGenericAlias):
            origin_type = typing.get_origin(dep_type)

            # `list[Service]`: list + (Service,)
            if isinstance(origin_type, type):
                if issubclass(origin_type, Sequence) or origin_type is collections.abc.Iterable:
                    real_dep_type = typing.get_args(dep_type)[0]  # such as `Service`
                    return _ListProvider(
                        self._get_or_make_provider_from_registry(
                            real_dep_type,
                            _require_unique=False,
                        )
                    )

                else:  # such as `dict[str, Service]`
                    raise UnsupportedContainerTypeError(dep_type)

            else:
                raise UnrecognizableDependencyTypeError(dep_type)

        # Eg: `Service | None`
        elif isinstance(dep_type, types.UnionType):
            dep_type_args = typing.get_args(dep_type)  # such as `(Service, types.NoneType)`

            is_optional = False
            found_dep: _Provider[Any] | None = None

            # find first managed dependency
            for possible_dep_type in dep_type_args:
                if possible_dep_type is types.NoneType:
                    is_optional = True
                else:
                    try:
                        found_dep = self._get_or_make_provider_from_registry(possible_dep_type)
                        break
                    except DiError:
                        pass

            if found_dep is None:
                if not is_optional:
                    raise DependencyNotFoundError(dep_type)
                else:
                    return _ObjectProvider(None)
            else:
                return found_dep

        elif isinstance(dep_type, type):  # such as `Service`
            pass

        else:
            raise UnrecognizableDependencyTypeError(dep_type)

        # Here we will get:
        # _FakeTypingGenericAlias: `App[Service]`
        # type: `Service`

        existed_providers = self._get_providers_from_container(dep_type)

        if existed_providers is None:
            # create new provider
            ctor_to_option = self._proto_to_ctor_to_option.get(dep_type)
            if ctor_to_option is None:
                raise DependencyNotFoundError(dep_type)

            providers: list[_Provider[Any]] = []
            for ctor, option in ctor_to_option.items():
                provider = self._ctor_to_provider.get(ctor)

                if provider is None:
                    provider = self._make_provider(ctor, option)

                    self._ctor_to_provider[ctor] = provider

                providers.append(provider)

            provider_key = self._calc_provider_key(dep_type)
            self._container[provider_key] = providers
            existed_providers = providers

        if _require_unique:
            if len(existed_providers) != 1:
                raise NoUniqueDependencyError(dep_type)
            else:
                return existed_providers[0]

        else:
            return existed_providers

    def _make_provider(
        self,
        ctor: Callable[..., Any],
        option: DependencyOption,
    ) -> _Provider[Any]:
        match option.provider_type:
            case "singleton":
                provider_class = _SingletonProvider
            case "factory":
                provider_class = _FactoryProvider

        if is_dataclass(ctor):
            assert isinstance(ctor, type)
            provider = self._make_provider_by_dataclass(ctor, provider_class)
        else:
            provider = self._make_provider_by_func(ctor, provider_class)

        return provider

    def _make_provider_by_dataclass(
        self,
        ctor: type,
        provider_class: type[_ConstructableProvider[Any]],
    ) -> _Provider[Any]:
        params = fields(ctor)
        annotations = typing.get_type_hints(ctor)
        kwargs: dict[str, _Provider[Any]] = {}

        for p in params:
            if not p.init:
                continue

            if p.default is not MISSING or p.default_factory is not MISSING:
                continue

            annotation = annotations[p.name]

            param_value = self._get_or_make_provider_from_registry(annotation)

            kwargs[p.name] = param_value

        return provider_class(ctor, **kwargs)

    def _make_provider_by_func(
        self,
        ctor: Callable[..., Any],
        provider_class: type[_ConstructableProvider[Any]],
    ) -> _Provider[Any]:
        params = list(signature(ctor, eval_str=True).parameters.values())

        args: list[Any] = []
        kwargs: dict[str, _Provider[Any]] = {}

        for p in params:
            annotation = p.annotation

            if annotation is Parameter.empty:
                raise ParameterNotAnnotatedError(ctor, p)

            param_value = self._get_or_make_provider_from_registry(annotation)

            match p.kind:
                case Parameter.VAR_KEYWORD:
                    raise VarKeywordParameterNotSupportedError(ctor, p)

                case Parameter.VAR_POSITIONAL:
                    raise VarPositionalParameterNotSupportedError(ctor, p)

                case Parameter.POSITIONAL_ONLY:
                    args.append(param_value)

                case Parameter.POSITIONAL_OR_KEYWORD:
                    args.append(param_value)

                case Parameter.KEYWORD_ONLY:
                    kwargs[p.name] = param_value

        return provider_class(ctor, *args, **kwargs)

    @staticmethod
    def _gather_dep_def_from_modules(
        modules: collections.abc.Iterable[types.ModuleType],
    ) -> list[tuple[type | Callable[..., Any], DependencyOption]]:
        dep_def_list: list[
            tuple[
                type | Callable[..., Any],
                DependencyOption,
            ]
        ] = []

        for module in modules:
            for mod_info in walk_packages(module.__path__, f"{module.__name__}."):
                mod = import_module(mod_info.name)
                for name, member in getmembers(mod):
                    if name.startswith("_"):
                        continue

                    if not _ManagedWrapper.is_managed(member):
                        continue

                    option = _ManagedWrapper.get_dependency_option(member)

                    dep_def_list.append((member, option))

        return dep_def_list

    @staticmethod
    def _get_prototypes_by_ctor(
        ctor: Callable[..., Any],
    ) -> list[_MonadPrimitiveMetaTypes]:
        if isinstance(ctor, type):
            ret_type = _lint_type_to_run_type(ctor)

        else:
            ret_type = typing.get_type_hints(ctor).get("return", MISSING)

            if ret_type is MISSING:
                raise ReturnTypeNotAnnotatedError(ctor)

            if not isinstance(ret_type, _PrimitiveMetaTypes):
                raise ReturnTypeIsNonTypeError(ctor)

        if ret_type is types.NoneType:
            raise ReturnTypeIsNoneError(ctor)

        if isinstance(ret_type, _MetaTypesUnionType):
            raise ReturnTypeIsUnionError(ctor)

        return _get_bases(ret_type)

    @staticmethod
    def _get_prototypes_by_val(
        v: object,
    ) -> list[_MonadPrimitiveMetaTypes]:
        return _get_bases(_MetaType(v))


# ***************************************************
# ******* lint-time & run-time Type Hierarchy *******
# ***************************************************
# *
# ** type
# ** | >> instance: int
# ** |
# ** | >> instance: _MetaTypingGenericAlias
# ** |              | >>> instance: typing.List[int], UserDefined_GenericType[int]
# ** |              |
# ** |              |- _MetaTypingAnnotatedAlias
# ** |                 | >>> instance: Annotated[int, 1], Annotated[list[int], 1]
# ** |
# ** | >> instance: _MetaTypesGenericAlias
# ** |              | >>> instance: list[int]
# ** |
# ** | >> instance: _MetaTypesUnionType
# **                | >>> instance: int | str


if TYPE_CHECKING:
    # ! type checker cannot correctly convert literal lint-time type to runtime type,
    # ! so we make these typing-check-only classes here for distinction when lint ourself.

    class _MetaType(type): ...

    class _MetaTypingGenericAlias(metaclass=_MetaType): ...

    class _MetaTypingAnnotatedAlias(_MetaTypingGenericAlias): ...

    class _MetaTypesGenericAlias(metaclass=_MetaType): ...

    class _MetaTypesUnionType(metaclass=_MetaType): ...

    class _Generic[*Ts](metaclass=_MetaType): ...

    class _InstanceOfMetaType(metaclass=_MetaType): ...

    class _object(_InstanceOfMetaType): ...  # noqa: N801

    class _InstanceOfMetaTypingGenericAlias(metaclass=_MetaTypingGenericAlias): ...  # pyright: ignore[reportGeneralTypeIssues]

    class _InstanceOfMetaTypesGenericAlias(metaclass=_MetaTypesGenericAlias): ...  # pyright: ignore[reportGeneralTypeIssues]

    class _InstanceOfMetaTypesUnionType(metaclass=_MetaTypesUnionType): ...  # pyright: ignore[reportGeneralTypeIssues]

    type _InstanceOfGenericPrimitiveMetaTypes = (
        _InstanceOfMetaTypingGenericAlias | _InstanceOfMetaTypesGenericAlias
    )
    type _InstanceOfMonadPrimitiveMetaTypes = (
        _InstanceOfGenericPrimitiveMetaTypes | _InstanceOfMetaType
    )
    type _InstanceOfPrimitiveMetaTypes = (
        _InstanceOfMonadPrimitiveMetaTypes | _InstanceOfMetaTypesUnionType
    )

else:
    _MetaType = type
    _MetaTypingGenericAlias = type(typing.Iterable[int])
    _MetaTypingAnnotatedAlias = type(typing.Annotated[int, 1])
    _MetaTypesGenericAlias = types.GenericAlias
    _MetaTypesUnionType = types.UnionType
    _object = object
    _Generic = typing.Generic


_GenericPrimitiveMetaTypes = _MetaTypingGenericAlias | _MetaTypesGenericAlias
_MonadPrimitiveMetaTypes = _GenericPrimitiveMetaTypes | _MetaType
_PrimitiveMetaTypes = _MonadPrimitiveMetaTypes | _MetaTypesUnionType


@overload
def _my_get_origin(t: _GenericPrimitiveMetaTypes, /) -> _MetaType: ...


@overload
def _my_get_origin(t: _MetaTypesUnionType, /) -> type[_MetaTypesUnionType]: ...


@overload
def _my_get_origin(t: _MetaType, /) -> None: ...


def _my_get_origin(t: _PrimitiveMetaTypes, /) -> Any:
    return typing.get_origin(t)


@overload
def _my_get_args(
    t: _MetaTypesUnionType,
    /,
) -> tuple[type[_InstanceOfMonadPrimitiveMetaTypes], ...]: ...


@overload
def _my_get_args(
    t: _PrimitiveMetaTypes,
    /,
) -> tuple[type[_InstanceOfPrimitiveMetaTypes], ...]: ...


def _my_get_args(
    t: _PrimitiveMetaTypes,
    /,
) -> tuple[type[_InstanceOfPrimitiveMetaTypes], ...]:
    return typing.get_args(t)


@overload
def _instance_of_meta(
    m: _MetaTypingGenericAlias,
) -> type[_InstanceOfMetaTypingGenericAlias]: ...


@overload
def _instance_of_meta(
    m: _MetaTypesGenericAlias,
) -> type[_InstanceOfMetaTypesGenericAlias]: ...


@overload
def _instance_of_meta(
    m: _MetaTypesUnionType,
) -> type[_InstanceOfMetaTypesUnionType]: ...


@overload
def _instance_of_meta(m: _MetaType) -> type[_InstanceOfMetaType]: ...


def _instance_of_meta(m: _PrimitiveMetaTypes) -> type[_InstanceOfPrimitiveMetaTypes]:
    return typing.cast(Any, m)


def _lint_type_to_run_type(t: type | types.UnionType) -> _PrimitiveMetaTypes:
    return typing.cast(Any, t)


@overload
def _run_type_val_to_lint_type_val[T](
    v: Sequence[_InstanceOfPrimitiveMetaTypes],
    _: type[T],
) -> list[T]: ...


@overload
def _run_type_val_to_lint_type_val[T](
    v: _InstanceOfPrimitiveMetaTypes,
    _: type[T],
) -> T: ...


def _run_type_val_to_lint_type_val[T](
    v: _InstanceOfPrimitiveMetaTypes | Sequence[_InstanceOfPrimitiveMetaTypes],
    _: type[T],
) -> T | list[T]:
    if isinstance(v, Sequence):
        if isinstance(v, list):
            return typing.cast(list[T], v)
        else:
            return typing.cast(list[T], list(v))

    else:
        return typing.cast(T, v)


def _unpack_if_union(
    t: _PrimitiveMetaTypes,
) -> tuple[type[_InstanceOfMonadPrimitiveMetaTypes], ...]:
    match t:
        case _MetaTypesUnionType():
            return _my_get_args(t)
        case _:
            return (_instance_of_meta(t),)


def _my_get_original_bases(
    t: _MonadPrimitiveMetaTypes,
) -> tuple[_MonadPrimitiveMetaTypes, ...]:
    return types.get_original_bases(_instance_of_meta(t))


def _get_bases(t: _MonadPrimitiveMetaTypes) -> list[_MonadPrimitiveMetaTypes]:
    result: collections.OrderedDict[_MonadPrimitiveMetaTypes, typing.Literal[True]] = (
        collections.OrderedDict()
    )

    stack = [t]
    has_generic = False

    while stack:
        curr = stack.pop()

        # handle curr self

        if curr is _Generic:
            has_generic = True
            continue
        if curr is _object:
            continue

        orig = _my_get_origin(curr)

        if orig is not None:
            if orig is not _Generic:
                result[curr] = True
                result[orig] = True
            else:
                has_generic = True
                continue

        else:
            result[curr] = True

        # handle the bases of curr

        if not isinstance(curr, _GenericPrimitiveMetaTypes):
            orig_bases = _my_get_original_bases(curr)
            stack.extend(reversed(orig_bases))  # deep-first

        else:
            # >>> assert orig is not None
            if typing.TYPE_CHECKING:  # just for typing correctness
                orig = _my_get_origin(curr)

            stack.append(orig)

    if has_generic:
        result[_Generic] = True

    result[_object] = True

    return list(result)


# ********************************
# ******* Error Definition *******
# ********************************


class DiError(Exception): ...


class DependencyNotFoundError(DiError):
    def __init__(self, dep_type: _PrimitiveMetaTypes) -> None:
        super().__init__(f"Dependency not found for {dep_type}")


class NoUniqueDependencyError(DiError):
    def __init__(self, dep_type: type | _MetaTypingGenericAlias) -> None:
        super().__init__(f"No unique dependency for type {dep_type}")


class UnrecognizableDependencyTypeError(DiError):
    def __init__(self, dep_type: Any) -> None:
        super().__init__(f"Unrecognizable dependency type {dep_type}")


class UnsupportedContainerTypeError(DiError):
    def __init__(self, dep_type: Any) -> None:
        super().__init__(f"Unsupported container type {dep_type}")


class ParameterNotAnnotatedError(DiError):
    def __init__(self, func: Callable[..., Any], param: Parameter) -> None:
        super().__init__(f"Parameter `{param.name}` of function `{func}` is not annotated")


class VarKeywordParameterNotSupportedError(DiError):
    def __init__(self, func: Callable[..., Any], param: Parameter) -> None:
        super().__init__(
            f"VAR_KEYWORD parameter `{param.name}` of function `{func}` is not supported"
        )


class VarPositionalParameterNotSupportedError(DiError):
    def __init__(self, func: Callable[..., Any], param: Parameter) -> None:
        super().__init__(
            f"VAR_POSITIONAL parameter `{param.name}` of function `{func}` is not supported"
        )


class ReturnTypeNotAnnotatedError(DiError):
    def __init__(self, func: Callable[..., Any]) -> None:
        super().__init__(f"Return of function `{func}` is not annotated")


class ReturnTypeIsNoneError(DiError):
    def __init__(self, func: Callable[..., Any]) -> None:
        super().__init__(f"Return of function `{func}` cannot be None or types.NoneType")


class ReturnTypeIsUnionError(DiError):
    def __init__(self, func: Callable[..., Any]) -> None:
        super().__init__(f"Return of function `{func}` cannot be types.UnionType")


class ReturnTypeIsNonTypeError(DiError):
    def __init__(self, func: Callable[..., Any]) -> None:
        super().__init__(f"Return of function `{func}` must be type")


class ConstructorExistsError(DiError):
    def __init__(self, func: Callable[..., Any]) -> None:
        super().__init__(f"Constructor `{func}` has already registered")


# ***********************************
# ******* Utilities & Helpers *******
# ***********************************


def _first[T](c: Sequence[T]) -> T:
    return next(iter(c))
