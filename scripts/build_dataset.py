"""가상 안전점검 사례 100건과 사전 정답을 생성하고 고정(lock)합니다.

- 모든 사례는 가상의 제조 사업장을 가정해 직접 작성한 교육용 문장입니다. 실제 회사 자료가 아닙니다.
- 이 스크립트는 정답 파일이 이미 있으면 덮어쓰지 않습니다(--force 없이는 중단).
- 모델 입력 파일(cases.jsonl)과 정답 파일(gold.jsonl)을 분리해, 모델은 정답을 보지 못합니다.
- 정답은 위험요인 분류의 일관성을 확인하기 위한 교육용 기준이며 법적 적합성 판정이 아닙니다.

정보 부족(insufficient_information) 정의:
  (1) 원문만으로 위험요인의 존재·종류를 특정할 수 없거나(모호한 진술, 진술자 스스로 불확실),
  (2) 장소 입력과 원문 어디에서도 장소를 알 수 없는 경우 True.
  위험요인이 '누출 의심'처럼 의심 수준으로 특정된 경우는 그 코드로 기록하고 (1)에 해당하지 않습니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from safety_assistant.config import load_settings, load_taxonomy  # noqa: E402

# (id, 장소, 원문, 정답 코드, 정보부족, 확인 필요 사항, 난이도, 어려운 사례 유형)
N, H = "normal", "hard"
CASES = [
    # ---- 전기 ----
    (1, "조립2라인", "조립2라인 배전반 앞에 빈 박스랑 팔레트가 쌓여 있어서 배전반 문을 열 수가 없습니다.", ["ELEC_PANEL_BLOCKED"], False, [], N, []),
    (2, "창고동", "창고 배전반 바로 앞에 원자재 포대가 놓여 있어요.", ["ELEC_PANEL_BLOCKED"], False, [], N, []),
    (3, "도장공장 입구", "분전반 앞에 청소도구 카트를 세워 둬서 분전반 접근이 막혀 있습니다.", ["ELEC_PANEL_BLOCKED"], False, [], N, []),
    (4, "프레스실", "프레스 옆 바닥에 깔린 전선 피복이 벗겨져서 안에 구리선이 보입니다.", ["ELEC_CABLE_DAMAGED"], False, [], N, []),
    (5, "용접부스 3번", "용접기 전원 케이블 중간이 찢어져서 절연테이프로 감아 놨는데 테이프가 다 떨어졌어요.", ["ELEC_CABLE_DAMAGED"], False, [], N, []),
    (6, "사출동", "사출기 뒤쪽 케이블이 지게차에 눌렸는지 피복이 갈라져 있습니다.", ["ELEC_CABLE_DAMAGED"], False, [], N, []),
    (7, "컴프레서실", "컴프레서실 제어반 문이 열린 채로 있고 안쪽 단자가 그대로 보입니다.", ["ELEC_PANEL_OPEN"], False, [], N, []),
    (8, "2층 기계실", "분전반 커버가 떼어져 바닥에 놓여 있어요. 충전부가 노출돼 있습니다.", ["ELEC_PANEL_OPEN"], False, [], N, []),
    (9, "품질검사실", "검사실 책상 밑에 멀티탭 하나에 또 멀티탭을 꽂아서 히터랑 장비 여러 개가 연결돼 있습니다.", ["ELEC_OVERLOAD"], False, [], N, []),
    (10, "휴게실", "휴게실 멀티탭에 전기포트, 전자레인지, 커피머신이 한꺼번에 꽂혀 있고 멀티탭이 따뜻합니다.", ["ELEC_OVERLOAD"], False, [], N, []),
    # ---- 넘어짐 ----
    (11, "자재창고", "자재창고 주통로에 철판 묶음이 내려져 있어서 사람이 옆으로 비켜 다녀야 합니다.", ["TRIP_WALKWAY_OBSTRUCTED"], False, [], N, []),
    (12, "포장라인", "포장라인 앞 보행통로에 빈 파렛트가 두 개 방치되어 있어요.", ["TRIP_WALKWAY_OBSTRUCTED"], False, [], N, []),
    (13, "가공동", "가공동 통로에 에어호스가 가로질러 늘어져 있어 발에 걸립니다.", ["TRIP_WALKWAY_OBSTRUCTED"], False, [], N, []),
    (14, "출하장", "출하장 비상구 쪽 통로에 반품 박스가 쌓여서 통로가 반쯤 막혔습니다.", ["TRIP_WALKWAY_OBSTRUCTED"], False, [], N, []),
    (15, "가공동 CNC 구역", "CNC 3호기 앞 바닥에 절삭유가 고여서 미끄럽습니다.", ["SLIP_FLOOR_WET"], False, [], N, []),
    (16, "식당 출입구", "식당 입구 바닥에 물이 흥건해요. 아까 한 분 미끄러질 뻔했습니다.", ["SLIP_FLOOR_WET"], False, [], N, []),
    (17, "유압실", "유압유가 바닥에 번져 있어서 걸을 때 미끄러워요.", ["SLIP_FLOOR_WET"], False, [], N, []),
    (18, "물류동", "물류동 바닥 에폭시가 깨져서 움푹 파인 곳이 있습니다. 대차 바퀴가 걸려요.", ["TRIP_FLOOR_DAMAGED"], False, [], N, []),
    (19, "정문 보행로", "보행로 배수구 덮개가 들려 있어 단차가 생겼습니다.", ["TRIP_FLOOR_DAMAGED"], False, [], N, []),
    (20, "조립1라인", "라인 옆 바닥 철판이 들떠서 턱이 생겼어요.", ["TRIP_FLOOR_DAMAGED"], False, [], N, []),
    # ---- 화학물질 ----
    (21, "세척실", "세척실에 있는 20리터 말통 두 개에 아무 표시가 없어서 무슨 액체인지 모르겠습니다.", ["CHEM_LABEL_MISSING"], False, ["용기 내용물"], N, []),
    (22, "도장공장", "도장실 선반에 옮겨 담은 시너 용기에 이름표가 없습니다.", ["CHEM_LABEL_MISSING"], False, [], N, []),
    (23, "실험실", "실험실 시약병 몇 개 라벨이 떨어져 나갔어요.", ["CHEM_LABEL_MISSING"], False, ["해당 시약병 수량과 내용물"], N, []),
    (24, "약품창고", "약품창고 드럼통 아래에 액체가 흘러나와 고여 있습니다.", ["CHEM_LEAK_SUSPECTED"], False, ["누출 물질 종류"], N, []),
    (25, "도금라인", "도금조 배관 연결부에서 액이 한 방울씩 떨어지고 있어요.", ["CHEM_LEAK_SUSPECTED"], False, [], N, []),
    (26, "폐액 보관장", "폐액통 옆면에 흘러내린 자국이 새로 생겼습니다.", ["CHEM_LEAK_SUSPECTED"], False, ["현재도 누출 중인지 여부"], N, []),
    (27, "도장공장", "도장 부스 옆 시너 통 뚜껑이 열린 채로 있어요.", ["CHEM_CONTAINER_OPEN"], False, [], N, []),
    (28, "세척실", "세척제 통 마개를 안 닫고 퇴근했는지 아침에 와 보니 열려 있었습니다.", ["CHEM_CONTAINER_OPEN"], False, [], N, []),
    (29, "접착공정", "접착제 캔 뚜껑이 열려 있고 냄새가 많이 납니다.", ["CHEM_CONTAINER_OPEN"], False, [], N, []),
    # ---- 기계 ----
    (30, "가공동", "연삭기 숫돌 덮개가 떼어진 상태로 돌아가고 있습니다.", ["MACH_GUARD_ISSUE"], False, [], N, []),
    (31, "컨베이어 2구간", "컨베이어 구동 체인 덮개가 빠져서 체인이 그대로 드러나 있습니다.", ["MACH_GUARD_ISSUE"], False, [], N, []),
    (32, "프레스실", "벨트 풀리 방호커버 볼트가 빠져서 커버가 덜렁거립니다.", ["MACH_GUARD_ISSUE"], False, [], N, []),
    (33, "조립2라인", "2라인 비상정지 버튼을 눌렀는데 라인이 바로 안 멈췄습니다.", ["MACH_ESTOP_ISSUE"], False, [], N, []),
    (34, "포장라인", "포장기 비상정지 버튼 캡이 깨져 있고 눌러도 잘 안 들어가요.", ["MACH_ESTOP_ISSUE"], False, [], N, []),
    (35, "컨베이어 1구간", "컨베이어 비상정지 줄(풀코드)이 끊어져 있습니다.", ["MACH_ESTOP_ISSUE"], False, [], N, []),
    (36, "사출동", "사출기 5호기에서 아침부터 끼익거리는 소리가 계속 납니다.", ["MACH_ABNORMAL_NOISE"], False, [], N, []),
    (37, "펌프실", "냉각수 펌프에서 평소보다 진동이 심하고 덜덜거리는 소리가 나요.", ["MACH_ABNORMAL_NOISE"], False, [], N, []),
    (38, "컴프레서실", "작업 중에 컴프레서에서 쇠 긁히는 소리가 간헐적으로 들립니다.", ["MACH_ABNORMAL_NOISE"], False, [], N, []),
    # ---- 환기 ----
    (39, "용접부스 2번", "용접부스 국소배기 후드가 흡입을 거의 안 하는 것 같아요. 연기가 위로 안 빨려 들어갑니다.", ["VENT_LEV_MALFUNCTION"], False, [], N, []),
    (40, "도장공장", "도장부스 배기팬이 멈춰 있습니다.", ["VENT_LEV_MALFUNCTION"], False, [], N, []),
    (41, "세척실", "세척조 위 배기덕트에 구멍이 뚫려 있어 흡입이 약합니다.", ["VENT_LEV_MALFUNCTION"], False, [], N, []),
    # ---- 가스 ----
    (42, "용접부스 1번", "산소 용기가 체인 없이 벽에 그냥 세워져 있습니다.", ["GAS_CYLINDER_UNSECURED"], False, [], N, []),
    (43, "가스 보관소", "아르곤 용기 세 개가 고정 없이 서 있고 하나는 기울어져 있어요.", ["GAS_CYLINDER_UNSECURED"], False, [], N, []),
    (44, "정비실", "아세틸렌 용기를 고정대 없이 카트 옆에 세워 뒀습니다.", ["GAS_CYLINDER_UNSECURED"], False, [], N, []),
    (45, "옥외 가스 보관소", "LPG 용기가 햇빛 드는 곳에 그대로 놓여 있습니다.", ["GAS_CYLINDER_STORAGE"], False, [], N, []),
    (46, "가스 보관소", "산소 용기와 아세틸렌 용기가 칸막이 없이 붙어서 보관돼 있어요.", ["GAS_CYLINDER_STORAGE"], False, [], N, []),
    (47, "정비실", "빈 질소 용기에 보호캡이 안 씌워져 있습니다.", ["GAS_CYLINDER_STORAGE"], False, [], N, []),
    (48, "보일러실", "보일러실 가스 배관 근처에서 가스 냄새가 납니다.", ["GAS_LEAK_SUSPECTED"], False, ["누출 지점"], N, []),
    (49, "용접부스 1번", "산소 레귤레이터 연결부에 비눗물 바르니 거품이 올라옵니다.", ["GAS_LEAK_SUSPECTED"], False, [], N, []),
    (50, "식당 주방", "주방 가스레인지 밸브 쪽에서 쉬익 소리와 냄새가 납니다.", ["GAS_LEAK_SUSPECTED"], False, [], N, []),
    # ---- 화재 ----
    (51, "자재창고", "소화기 앞에 자재가 쌓여서 소화기를 꺼낼 수 없습니다.", ["FIRE_EXTINGUISHER_ISSUE"], False, [], N, []),
    (52, "2층 사무동", "복도 소화기 압력게이지 바늘이 빨간 구간에 있습니다.", ["FIRE_EXTINGUISHER_ISSUE"], False, [], N, []),
    (53, "도장공장", "도장실 입구 소화기 거치대가 비어 있어요. 소화기가 없습니다.", ["FIRE_EXTINGUISHER_ISSUE"], False, [], N, []),
    (54, "용접부스 3번", "용접하는 바로 옆에 시너 통과 걸레가 놓여 있습니다.", ["FIRE_FLAMMABLE_NEAR_IGNITION"], False, [], N, []),
    (55, "정비실", "그라인더 작업대 옆에 휘발유 통이 있어요. 불티가 튑니다.", ["FIRE_FLAMMABLE_NEAR_IGNITION"], False, [], N, []),
    (56, "보일러실", "보일러 옆에 폐유 묻은 걸레 더미를 쌓아 놨습니다.", ["FIRE_FLAMMABLE_NEAR_IGNITION"], False, [], N, []),
    # ---- 추락 ----
    (57, "2층 메자닌", "메자닌 가장자리 난간 한 칸이 빠져 있습니다.", ["FALL_OPENING_UNGUARDED"], False, [], N, []),
    (58, "피트 구역", "설비 피트 덮개를 열어 놓고 주변에 표시나 펜스가 없어요.", ["FALL_OPENING_UNGUARDED"], False, [], N, []),
    (59, "옥상", "옥상 채광창 주변에 안전망이나 난간이 없습니다.", ["FALL_OPENING_UNGUARDED"], False, [], N, []),
    (60, "자재창고", "선반 위 자재 꺼낼 때 쓰는 A형 사다리 다리 고무패킹이 빠져서 흔들립니다.", ["FALL_LADDER_UNSTABLE"], False, [], N, []),
    (61, "조명 교체 구역", "이동식 비계 바퀴 고정장치가 안 걸려 있어 밀립니다.", ["FALL_LADDER_UNSTABLE"], False, [], N, []),
    (62, "정비실", "사다리 발판 하나가 휘어 있어요.", ["FALL_LADDER_UNSTABLE"], False, [], N, []),
    # ---- 보호구 ----
    (63, "연삭 작업장", "연삭 작업자가 보안경 없이 작업하고 있습니다.", ["PPE_NOT_WORN"], False, [], N, []),
    (64, "도장공장", "도장 작업 중인 인원이 방독마스크를 안 쓰고 있어요.", ["PPE_NOT_WORN"], False, [], N, []),
    (65, "프레스실", "프레스실에서 귀마개 안 하고 작업하는 사람이 있습니다.", ["PPE_NOT_WORN"], False, [], N, []),
    # ---- 정상 보고 ----
    (66, "조립1라인", "조립1라인 순회 점검했는데 특이사항 없습니다.", ["NONE"], False, [], N, []),
    (67, "자재창고", "창고 통로, 소화기, 배전반 확인했고 이상 없었습니다.", ["NONE"], False, [], N, []),
    (68, "가스 보관소", "가스 보관소 점검 완료, 이상 없음.", ["NONE"], False, [], N, []),
    (69, "포장라인", "오늘 포장라인 점검 결과 위험요인 발견되지 않았습니다.", ["NONE"], False, [], N, []),
    (70, "사무동", "사무동 비상구 정상 개방, 유도등 점등 확인했습니다.", ["NONE"], False, [], N, []),
    # ---- 위험요인 2개(단순 복합) ----
    (71, "가공동", "CNC 2호기 앞 바닥에 절삭유가 흘러 있고 칩 박스가 통로에 나와 있습니다.", ["SLIP_FLOOR_WET", "TRIP_WALKWAY_OBSTRUCTED"], False, [], N, []),
    (72, "용접부스 2번", "용접부스 배기가 약하고 작업자가 용접면 없이 보안경만 쓰고 있어요.", ["VENT_LEV_MALFUNCTION", "PPE_NOT_WORN"], False, [], N, []),
    (73, "세척실", "세척제 통 뚜껑이 열려 있고 라벨도 없습니다.", ["CHEM_CONTAINER_OPEN", "CHEM_LABEL_MISSING"], False, [], N, []),
    (74, "프레스실", "프레스 방호덮개가 빠져 있고, 그 옆 전선 피복도 벗겨져 있습니다.", ["MACH_GUARD_ISSUE", "ELEC_CABLE_DAMAGED"], False, [], N, []),
    (75, "가스 보관소", "산소 용기가 고정 없이 서 있고 캡도 없습니다.", ["GAS_CYLINDER_UNSECURED", "GAS_CYLINDER_STORAGE"], False, [], N, []),
    (76, "자재창고", "배전반 앞과 소화기 앞에 박스가 쌓여 있습니다.", ["ELEC_PANEL_BLOCKED", "FIRE_EXTINGUISHER_ISSUE"], False, [], N, []),
    (77, "정비실", "사다리가 흔들거리고, 사다리 올라가는 사람이 안전모를 안 썼습니다.", ["FALL_LADDER_UNSTABLE", "PPE_NOT_WORN"], False, [], N, []),
    (78, "펌프실", "펌프에서 평소와 다른 진동이 있고, 펌프 아래 바닥에 윤활유가 번져 미끄럽습니다.", ["MACH_ABNORMAL_NOISE", "SLIP_FLOOR_WET"], False, [], N, []),
    (79, "출하장", "지게차 통로에 파렛트가 나와 있고, 바닥 배수구 덮개도 깨져 있어요.", ["TRIP_WALKWAY_OBSTRUCTED", "TRIP_FLOOR_DAMAGED"], False, [], N, []),
    (80, "접착공정", "접착제 캔이 열려 있고, 국소배기 팬이 꺼져 있습니다.", ["CHEM_CONTAINER_OPEN", "VENT_LEV_MALFUNCTION"], False, [], N, []),
    # ==== 어려운 사례 20건 ====
    # 복합 위험요인(3개 이상)
    (81, "도장공장", "도장실 들어가 보니 배기팬은 멈춰 있고, 시너 통 두 개 뚜껑이 열려 있었고, 하나는 라벨이 없었습니다. 입구 소화기 앞에도 페인트 캔이 쌓여 있어요.",
     ["VENT_LEV_MALFUNCTION", "CHEM_CONTAINER_OPEN", "CHEM_LABEL_MISSING", "FIRE_EXTINGUISHER_ISSUE"], False, [], H, ["복합"]),
    (82, "용접부스 3번", "용접부스에 아세틸렌 용기가 묶여 있지 않고, 용접기 케이블 피복이 찢어져 있고, 바로 옆에 기름 걸레가 있습니다.",
     ["GAS_CYLINDER_UNSECURED", "ELEC_CABLE_DAMAGED", "FIRE_FLAMMABLE_NEAR_IGNITION"], False, [], H, ["복합"]),
    (83, "자재창고", "창고 정리가 안 돼서 통로에 자재가 나와 있고 배전반 앞도 막혔어요. 위쪽 선반 자재 내리는 사다리도 한쪽 다리가 짧아 기우뚱합니다.",
     ["TRIP_WALKWAY_OBSTRUCTED", "ELEC_PANEL_BLOCKED", "FALL_LADDER_UNSTABLE"], False, [], H, ["복합"]),
    (84, "프레스실", "프레스 2호기 비상정지가 반응이 늦고, 덮개 한쪽이 없고, 작업자 두 명이 귀마개를 안 했습니다.",
     ["MACH_ESTOP_ISSUE", "MACH_GUARD_ISSUE", "PPE_NOT_WORN"], False, [], H, ["복합"]),
    (85, "보일러실", "보일러실 바닥에 물이 고여 있고, 가스 냄새가 약간 나는 것 같고, 제어반 문도 열려 있습니다.",
     ["SLIP_FLOOR_WET", "GAS_LEAK_SUSPECTED", "ELEC_PANEL_OPEN"], False, ["가스 누출 지점"], H, ["복합", "모호"]),
    (86, "2층 메자닌", "메자닌 난간 일부 빠져 있고 그 아래 통로에 자재 쌓여 있음. 메자닌 위 작업자 안전모 미착용.",
     ["FALL_OPENING_UNGUARDED", "TRIP_WALKWAY_OBSTRUCTED", "PPE_NOT_WORN"], False, [], H, ["복합"]),
    # 모호한 진술
    (87, "약품창고", "약품창고 바닥에 뭔가 젖어 있는데 물인지 약품인지 모르겠습니다.",
     ["CHEM_LEAK_SUSPECTED", "SLIP_FLOOR_WET"], True, ["젖은 액체가 물인지 약품인지", "누출원"], H, ["모호"]),
    (88, "가공동", "가공동 쪽에서 타는 냄새가 잠깐 났는데 어디서 나는지는 못 찾았습니다.",
     ["UNKNOWN_NEEDS_CHECK"], True, ["냄새 발생 위치", "발생원(설비·전기·기타)"], H, ["모호"]),
    (89, "사출동", "사출기 근처가 좀 이상한 느낌입니다. 확인 부탁드려요.",
     ["UNKNOWN_NEEDS_CHECK"], True, ["무엇이 이상한지(소리·냄새·누출 등)", "해당 사출기 번호"], H, ["모호"]),
    (90, "컴프레서실", "컴프레서실에서 소리가 나는데 원래 나는 소리인지 잘 모르겠어요.",
     ["MACH_ABNORMAL_NOISE"], True, ["평상시 소리와 다른지 여부", "해당 설비"], H, ["모호"]),
    (91, "세척실", "세척조 배기가 약한 것 같기도 하고 아닌 것 같기도 합니다. 냄새가 평소보다 조금 더 나요.",
     ["VENT_LEV_MALFUNCTION"], True, ["국소배기 풍량 측정값"], H, ["모호"]),
    # 부정문
    (92, "가스 보관소", "가스 용기는 전부 체인으로 고정돼 있고 캡도 씌워져 있습니다. 누출 냄새도 없습니다.",
     ["NONE"], False, [], H, ["부정문"]),
    (93, "가공동", "연삭기 덮개는 정상적으로 장착돼 있습니다. 다만 작업자가 보안경을 안 쓰고 있었습니다.",
     ["PPE_NOT_WORN"], False, [], H, ["부정문"]),
    (94, "조립2라인", "비상정지 버튼 테스트했는데 정상 작동했습니다. 배전반 앞도 비어 있고요. 그런데 라인 옆 통로에 부품 상자가 나와 있었어요.",
     ["TRIP_WALKWAY_OBSTRUCTED"], False, [], H, ["부정문"]),
    (95, "도장공장", "도장부스 배기는 잘 되고 시너 통도 다 닫혀 있는데, 소화기가 하나 안 보입니다.",
     ["FIRE_EXTINGUISHER_ISSUE"], False, [], H, ["부정문"]),
    (96, "출하장", "바닥이 미끄럽지는 않고, 파손된 곳도 없습니다. 특별한 위험은 못 봤습니다.",
     ["NONE"], False, [], H, ["부정문"]),
    # 정보 부족
    (97, "", "저쪽 기계 덮개가 빠져 있어요.",
     ["MACH_GUARD_ISSUE"], True, ["장소", "해당 기계"], H, ["정보부족"]),
    (98, "", "위험해 보이는 게 있어서 사진 찍었습니다.",
     ["UNKNOWN_NEEDS_CHECK"], True, ["장소", "무엇이 위험한지에 대한 설명"], H, ["정보부족"]),
    (99, "자재창고", "확인 필요.",
     ["UNKNOWN_NEEDS_CHECK"], True, ["확인이 필요한 대상과 상황"], H, ["정보부족"]),
    (100, "", "배전반 앞에 뭐가 있어요.",
     ["ELEC_PANEL_BLOCKED"], True, ["장소", "배전반 앞 물건의 종류"], H, ["정보부족"]),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="기존 정답 파일을 덮어씀(권장하지 않음)")
    args = ap.parse_args()

    settings = load_settings()
    tax = load_taxonomy()
    codes = tax["codes"]
    cases_path = ROOT / settings["paths"]["cases"]
    gold_path = ROOT / settings["paths"]["gold"]
    lock_path = ROOT / settings["paths"]["gold_lock"]

    if gold_path.exists() and not args.force:
        sys.exit(f"[중단] {gold_path} 가 이미 있습니다. 정답은 고정되어 있으므로 덮어쓰지 않습니다.")

    assert len(CASES) == 100, len(CASES)
    assert len({c[0] for c in CASES}) == 100
    assert sum(1 for c in CASES if c[6] == H) == 20

    cases_path.parent.mkdir(parents=True, exist_ok=True)
    with cases_path.open("w", encoding="utf-8") as fc, gold_path.open("w", encoding="utf-8") as fg:
        for cid, loc, text, gold_codes, insuff, missing, diff, tags in CASES:
            for c in gold_codes:
                assert c in codes, c
            case_id = f"C{cid:03d}"
            fc.write(json.dumps({"case_id": case_id, "location": loc, "text": text}, ensure_ascii=False) + "\n")
            cats = sorted({codes[c]["category"] for c in gold_codes})
            actions = [a for c in gold_codes for a in codes[c]["actions"]]
            fg.write(json.dumps({
                "case_id": case_id,
                "location": loc,
                "text": text,
                "hazard_category": cats,
                "hazard_codes": sorted(gold_codes),
                "observed_hazards": [codes[c]["name"] for c in gold_codes],
                "missing_information": missing,
                "suggested_actions": actions,
                "insufficient_information": insuff,
                "difficulty": diff,
                "hard_tags": tags,
            }, ensure_ascii=False) + "\n")

    lock = {
        "locked_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "모델 실행 전에 고정한 사전 정답. 평가 스크립트는 이 해시와 다르면 실행을 중단합니다.",
        "taxonomy_version": tax["version"],
        "files": {
            settings["paths"]["cases"]: sha256(cases_path),
            settings["paths"]["gold"]: sha256(gold_path),
        },
    }
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"사례 {len(CASES)}건 작성, 정답 고정: {lock_path}")


if __name__ == "__main__":
    main()
