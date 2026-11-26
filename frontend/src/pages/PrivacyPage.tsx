// 개인정보처리방침. 로그인 없이 볼 수 있어야 한다(Google OAuth 동의 화면에 링크된다).
// AuthGate가 PUBLIC_PATHS로 이 경로를 로그인·AppShell 밖에서 보여준다.
import { useEffect, type ReactNode } from "react";

const CONTACT = "joonyong.park.dev@gmail.com";
const EFFECTIVE_DATE = "2026년 11월 25일";

export default function PrivacyPage() {
  useEffect(() => {
    const prev = document.title;
    document.title = "개인정보처리방침 - Joomini";
    return () => {
      document.title = prev;
    };
  }, []);

  return (
    <main className="mx-auto max-w-2xl px-5 py-12 text-[15px] leading-7 text-gray-800 dark:text-gray-200">
      <a href="/" className="text-sm text-gray-500 hover:underline dark:text-gray-400">
        ← Joomini
      </a>
      <h1 className="mt-4 text-2xl font-semibold text-gray-900 dark:text-gray-100">개인정보처리방침</h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">시행일: {EFFECTIVE_DATE}</p>

      <p className="mt-6">
        Joomini(이하 &ldquo;서비스&rdquo;)는 질문에 맞는 AI 모델을 골라 답하는 개인 포트폴리오 프로젝트입니다. 이 문서는
        서비스가 어떤 정보를 왜 모으고, 어디에 보관하며, 누구에게 전달하는지 설명합니다.
      </p>

      <Section title="1. 수집하는 정보">
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <b>Google 계정 정보:</b> 이메일 주소, 이름 (Google 로그인 시 Amazon Cognito를 거쳐 받습니다)
          </li>
          <li>
            <b>서비스 이용 중 만든 정보:</b> 대화 내용, 업로드한 파일, 프로필 이미지, 프로젝트·설정, 대화에서 추출한
            메모리(사용자에 대한 기억)
          </li>
          <li>
            <b>운영 기록:</b> 오류 확인을 위한 서버 로그(요청 시각, 경로, 오류 내용)
          </li>
        </ul>
      </Section>

      <Section title="2. 이용 목적">
        <ul className="list-disc space-y-1 pl-5">
          <li>로그인과 사용자 구분</li>
          <li>질문에 대한 답변 생성, 대화·파일·메모리 기능 제공</li>
          <li>오류 확인과 서비스 안정성 유지</li>
        </ul>
        <p className="mt-2">광고, 프로파일링, 정보 판매에는 사용하지 않습니다.</p>
      </Section>

      <Section title="3. Google 사용자 데이터">
        <p>
          Google 로그인으로 받은 정보(이메일, 이름)는 계정을 만들고 구분하는 데에만 사용합니다. Google API로 받은 정보를
          다른 용도로 쓰거나 제3자에게 판매·양도하지 않으며, AI 모델 학습에 사용하지 않습니다. Google 계정의 메일, 드라이브
          등 다른 데이터에는 접근하지 않습니다.
        </p>
      </Section>

      <Section title="4. 보관 위치와 기간">
        <p>
          정보는 Amazon Web Services 서울 리전(Amazon DynamoDB, Amazon S3)에 암호화되어 저장됩니다. 대화와 파일은 사용자가
          삭제하거나 계정 삭제를 요청할 때까지 보관하고, 요청을 받으면 지체 없이 삭제합니다.
        </p>
      </Section>

      <Section title="5. 외부 서비스로 전달되는 정보">
        <p>답변을 만들기 위해 다음 서비스에 필요한 내용만 전달합니다.</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <b>AI 모델 제공자</b> (OpenAI, Google Gemini, Anthropic Claude): 질문, 대화 맥락, 첨부 파일 내용, 관련 메모리
          </li>
          <li>
            <b>웹 검색 서비스</b> (Tavily 등): 웹 검색이 필요한 질문의 검색어
          </li>
          <li>
            <b>Amazon Web Services:</b> 인증(Amazon Cognito), 저장, 서버 실행
          </li>
        </ul>
        <p className="mt-2">각 제공자는 자신의 개인정보처리방침과 API 데이터 정책에 따라 정보를 처리합니다.</p>
      </Section>

      <Section title="6. 쿠키">
        <p>
          로그인 상태를 유지하기 위한 쿠키(HttpOnly, 7일)만 사용합니다. 광고나 방문 추적을 위한 쿠키는 사용하지 않습니다.
          화면 테마(다크 모드) 설정은 브라우저에만 저장됩니다.
        </p>
      </Section>

      <Section title="7. 사용자의 권리">
        <p>
          언제든 자신의 정보 열람, 수정, 삭제, 계정 삭제를 요청할 수 있습니다. 대화는 서비스 안에서 직접 삭제할 수 있고, 그
          밖의 요청은 아래 연락처로 보내 주세요.
        </p>
      </Section>

      <Section title="8. 연락처">
        <p>
          개인정보 관련 문의: <a href={`mailto:${CONTACT}`} className="underline">{CONTACT}</a>
        </p>
      </Section>

      <Section title="9. 변경">
        <p>이 방침이 바뀌면 이 페이지에 시행일과 함께 알립니다.</p>
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}
