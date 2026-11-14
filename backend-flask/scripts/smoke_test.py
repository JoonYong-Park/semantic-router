"""DynamoDB Local에서 repo 계층의 조회 패턴(AP1~AP23)을 실제로 호출해 확인한다.

    docker compose -f docker-compose.serverless.yml run --rm flask python scripts/smoke_test.py

테스트마다 새 사용자 id를 써서 기존 로컬 데이터와 섞이지 않고, 끝나면 만든 데이터를 지운다.
실제 AWS 테이블에서는 돌리지 않는다(DYNAMODB_ENDPOINT가 없으면 중단).
"""

import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from botocore.exceptions import ClientError  # noqa: E402

from app.db import dynamo, keys  # noqa: E402
from app.db.repo import conversations as conv_repo  # noqa: E402
from app.db.repo import memory, projects, usage, users  # noqa: E402

if not os.environ.get("DYNAMODB_ENDPOINT"):
    sys.exit("DYNAMODB_ENDPOINT가 없습니다. 스모크 테스트는 DynamoDB Local에서만 돌립니다.")

passed = 0


def check(label: str, cond: bool, detail=None) -> None:
    global passed
    if not cond:
        print(f"FAIL  {label}  {detail if detail is not None else ''}")
        sys.exit(1)
    passed += 1
    print(f"ok    {label}")


uid = str(uuid.uuid4())
other = str(uuid.uuid4())

# AP1, AP2 -----------------------------------------------------------------
p = users.ensure_profile(uid, "tester@example.com", "테스터")
check("AP1 프로필 생성", p["email"] == "tester@example.com")
users.ensure_profile(uid, "changed@example.com", "바뀜")
check("AP1 프로필은 다시 만들어도 덮어쓰지 않음", users.get_profile(uid)["email"] == "tester@example.com")
users.set_avatar_updated(uid, dynamo.now_iso())
check("AP1 아바타 시각 설정", "avatar_updated_at" in users.get_profile(uid))
s = users.update_settings(uid, personal_instruction="짧게 답해줘")
check("AP2 개인 지침 저장", s["personal_instruction"] == "짧게 답해줘")
s = users.update_settings(uid, personal_instruction=None, imported_memory="가져온 메모리")
check("AP2 None이면 해당 값 삭제", "personal_instruction" not in s and s["imported_memory"] == "가져온 메모리")

# AP3, AP4 -----------------------------------------------------------------
m1 = memory.put_user_memory(uid, "나", "Profile", "개발자", ["2년차"])
time.sleep(0.01)
m2 = memory.put_user_memory(uid, "주제", "RAG", "검색 개선", ["리랭커"])
check("AP3 전역 메모리 전체 (최신순)", [m["name"] for m in memory.list_user_memory(uid)] == ["RAG", "Profile"])
check("AP3 카테고리별 조회", [m["name"] for m in memory.list_user_memory(uid, "나")] == ["Profile"])
memory.put_user_memory(uid, "나", "Profile", "개발자", ["2년차", "Python"], mem_id=m1["id"])
check("AP3 같은 id로 저장하면 수정", memory.list_user_memory(uid, "나")[0]["details"] == ["2년차", "Python"])
check("AP4 남의 메모리는 못 지움", memory.delete_memory_by_id(other, m2["id"]) is False)
check("AP4 id로 삭제", memory.delete_memory_by_id(uid, m2["id"]) is True)

# AP5, AP6 -----------------------------------------------------------------
c1 = conv_repo.create_conversation(uid, title="첫 대화")
time.sleep(0.01)
c2 = conv_repo.create_conversation(uid, title="두 번째 대화")
listed = conv_repo.list_conversations(uid)
check("AP5 대화 목록 최신순", [c["id"] for c in listed] == [c2["id"], c1["id"]], [c["title"] for c in listed])
conv_repo.update_conversation(uid, c1["id"], pinned=True)
check("AP5 고정한 대화가 맨 앞", conv_repo.list_conversations(uid)[0]["id"] == c1["id"])
check("AP6 남의 대화는 조회 안 됨", conv_repo.get_conversation(other, c1["id"]) is None)
conv_repo.update_conversation(uid, c2["id"], title="이름 바꿈")
check("AP6 제목 변경", conv_repo.get_conversation(uid, c2["id"])["title"] == "이름 바꿈")

# AP7~AP10 -----------------------------------------------------------------
a1 = conv_repo.create_attachment(uid, c2["id"], "회의록.docx", f"chats/{c2['id']}/a.docx",
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 1234)
check("AP13 업로드 첨부에는 ttl이 걸림", dynamo.get_item(keys.conv_pk(c2["id"]), keys.att_sk(a1["id"]))["ttl"] > time.time())
check("AP8 첫 메시지 전에는 message_count 0", conv_repo.get_conversation(uid, c2["id"])["message_count"] == 0)
user_msg = conv_repo.add_message(uid, c2["id"], "user", "회의록 정리해줘", attachment_ids=[a1["id"]])
time.sleep(0.01)
bot_msg = conv_repo.add_message(uid, c2["id"], "assistant", "정리했습니다.", model_used="gpt-6-luna",
                                complexity_score=0.07, input_tokens=120, output_tokens=40)
conv_after = conv_repo.get_conversation(uid, c2["id"])
check("AP8 메시지 2개 후 message_count 2", conv_after["message_count"] == 2)
check("AP10 메시지에 연결된 첨부는 ttl 제거", "ttl" not in dynamo.get_item(keys.conv_pk(c2["id"]), keys.att_sk(a1["id"])))
page = conv_repo.get_conversation_page(c2["id"])
check("AP7 메시지 시간순 + 첨부 묶음", [m["role"] for m in page] == ["user", "assistant"]
      and page[0]["attachments"][0]["id"] == a1["id"])
check("AP7 float는 Decimal로 저장 후 float로 복원", page[1]["complexity_score"] == 0.07)
check("AP5 메시지 저장 후 해당 대화가 최신순 맨 앞(고정 제외)",
      [c["id"] for c in conv_repo.list_conversations(uid) if not c.get("pinned")][0] == c2["id"])
conv_repo.update_message(c2["id"], user_msg["sk"], token_count=9)
check("AP9 token_count 수정", conv_repo.get_conversation_page(c2["id"])[0]["token_count"] == 9)
try:
    conv_repo.add_message(uid, c2["id"], "user", "남의 첨부", attachment_ids=[str(uuid.uuid4())])
    check("AP10 없는 첨부를 연결하면 트랜잭션 전체 취소", False)
except ClientError as e:
    check("AP10 없는 첨부를 연결하면 트랜잭션 전체 취소",
          e.response["Error"]["Code"] == "TransactionCanceledException"
          and conv_repo.get_conversation(uid, c2["id"])["message_count"] == 2)

# AP11, AP12 ---------------------------------------------------------------
check("AP11 id로 첨부 조회", conv_repo.get_attachment(uid, a1["id"])["original_name"] == "회의록.docx")
check("AP11 남의 첨부는 조회 안 됨", conv_repo.get_attachment(other, a1["id"]) is None)
check("AP12 대화 안 첨부 목록", [a["id"] for a in conv_repo.list_attachments(c2["id"])] == [a1["id"]])

# AP22 ---------------------------------------------------------------------
found = conv_repo.search(uid, "회의록")
check("AP22 본문 부분 일치 검색", found and found[0]["conversation_id"] == c2["id"], found)
check("AP22 제목 검색", conv_repo.search(uid, "첫 대화")[0]["conversation_id"] == c1["id"])

# AP23 ---------------------------------------------------------------------
check("AP23 커서 없으면 전체", len(conv_repo.messages_after(c2["id"], None)) == 2)
check("AP23 커서 이후만", [m["role"] for m in conv_repo.messages_after(c2["id"], user_msg["sk"])] == ["assistant"])

# AP15~AP19 ----------------------------------------------------------------
proj = projects.create_project(uid, "코드 품질 검증 RAG", instruction="표로 답해줘")
check("AP15 프로젝트 생성과 목록", [p["id"] for p in projects.list_projects(uid)] == [proj["id"]])
check("AP15 남의 프로젝트는 조회 안 됨", projects.get_project(other, proj["id"]) is None)
pc = conv_repo.create_conversation(uid, title="프로젝트 대화", project_id=proj["id"])
conv_repo.set_conversation_project(uid, c1["id"], proj["id"])
check("AP16 프로젝트 소속 대화", {c["id"] for c in conv_repo.list_project_conversations(proj["id"])} == {pc["id"], c1["id"]})
conv_repo.set_conversation_project(uid, c1["id"], None)
check("AP16 편입 해제", [c["id"] for c in conv_repo.list_project_conversations(proj["id"])] == [pc["id"]])
f1 = projects.add_project_file(uid, proj["id"], "가이드.md", f"projects/{proj['id']}/g.md", "text/markdown", 500,
                               extraction_status="done")
f2 = projects.add_project_file(uid, proj["id"], "표.xlsx", f"projects/{proj['id']}/t.xlsx", "application/xlsx", 700)
check("AP17 파일 용량 합계 카운터", projects.get_project(uid, proj["id"])["files_total_bytes"] == 1200)
check("AP17 추출 완료 파일만", [f["id"] for f in projects.list_project_files(proj["id"], done_only=True)] == [f1["id"]])
projects.delete_project_file(uid, f2["id"])
check("AP17 파일 삭제 시 용량 차감", projects.get_project(uid, proj["id"])["files_total_bytes"] == 500)
pm = memory.put_project_memory(uid, proj["id"], "평가 지표", "Hit Rate@5", ["0.58"])
check("AP18 프로젝트 메모리", memory.list_project_memory(proj["id"])[0]["id"] == pm["id"])
check("AP4 프로젝트 메모리도 id로 삭제", memory.delete_memory_by_id(uid, pm["id"]) is True)
conv_repo.add_message(uid, pc["id"], "user", "프로젝트 질문")
res = projects.delete_project(uid, proj["id"])
check("AP19 프로젝트 연쇄 삭제", projects.get_project(uid, proj["id"]) is None
      and conv_repo.get_conversation(uid, pc["id"]) is None
      and dynamo.query_partition(keys.conv_pk(pc["id"])) == []
      and dynamo.query_partition(keys.proj_pk(proj["id"])) == [], res)

# AP20, AP21 ---------------------------------------------------------------
now = datetime.now(timezone.utc)
usage.record_usage(uid, "gpt-6-luna", 100, 50, at=now)
usage.record_usage(uid, "gpt-6-luna", 10, 5, at=now)
usage.record_usage(uid, "claude-opus-5-5", 1000, 300, at=now - timedelta(days=2))
today = now.astimezone(usage.KST).date()
check("AP20 주기 합계", usage.used_since(uid, today - timedelta(days=5)) == 1465)
check("AP20 오늘부터 합계", usage.used_since(uid, today) == 165)
d = usage.daily(uid, today - timedelta(days=6), today)
check("AP21 일별 버킷과 모델별 합계", d["by_model"]["gpt-6-luna"]["calls"] == 2 and len(d["buckets"]) == 2, d)
h = usage.hourly(uid, today)
check("AP21 시간별(오늘)", sum(b["input"] for b in h["buckets"].values()) == 110, h)

# AP14 ---------------------------------------------------------------------
conv_repo.delete_conversation(uid, c2["id"])
check("AP14 대화 삭제 시 메시지와 첨부까지", conv_repo.get_conversation(uid, c2["id"]) is None
      and dynamo.query_partition(keys.conv_pk(c2["id"])) == [])

# 정리 ---------------------------------------------------------------------
for cid in [c["id"] for c in conv_repo.list_conversations(uid)]:
    conv_repo.delete_conversation(uid, cid)
dynamo.delete_keys(dynamo.query_partition(keys.user_pk(uid)))
check("정리: 테스트 사용자 데이터 삭제", dynamo.query_partition(keys.user_pk(uid)) == [])

print(f"\n모두 통과: {passed}개")
