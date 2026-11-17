"""설정 파일 로딩. 경로·모델·임계값은 config/*.yaml 에서만 읽습니다."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = Path(os.environ.get("SIA_SETTINGS", ROOT / "config" / "settings.yaml"))


@lru_cache(maxsize=1)
def load_settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    path = ROOT / load_settings()["paths"]["taxonomy"]
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def path_of(key: str) -> Path:
    """settings.paths 의 상대 경로를 프로젝트 루트 기준 절대 경로로 바꿉니다."""
    return ROOT / load_settings()["paths"][key]
