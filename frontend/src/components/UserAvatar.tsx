import { getAvatarUrl } from "@/lib/api";
import { User } from "@/lib/types";
import { useAuthedObjectUrl } from "@/lib/useAuthedObjectUrl";

// 프로필 사진이 있으면 사진, 없으면(또는 불러오기 실패하면) 이름 첫 글자 원형 아이콘.
// size는 Tailwind 크기 클래스(예: "h-7 w-7"), textSize는 글자 크기 클래스.
// 사진 API는 로그인 토큰이 필요해 <img src>에 URL을 바로 넣지 않고 blob URL로 받아 쓴다.
export default function UserAvatar({
  user,
  size = "h-7 w-7",
  textSize = "text-xs",
}: {
  user: User | null;
  size?: string;
  textSize?: string;
}) {
  const { src } = useAuthedObjectUrl(getAvatarUrl(user));

  if (src) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={src} alt="" className={`${size} shrink-0 rounded-full object-cover`} />
    );
  }
  const initial = (Array.from(user?.name ?? "사용자")[0] ?? "?").toUpperCase();
  return (
    <span
      className={`flex ${size} shrink-0 items-center justify-center rounded-full bg-gray-800 font-semibold text-white dark:bg-gray-100 dark:text-gray-900 ${textSize}`}
    >
      {initial}
    </span>
  );
}
