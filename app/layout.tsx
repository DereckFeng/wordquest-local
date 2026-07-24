import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WordQuest Local｜RAZ 原文听写",
  description: "局域网内使用的 RAZ 原文听写、个人单词本与拼写远征学习站。",
  applicationName: "WordQuest Local",
  openGraph: {
    title: "WordQuest Local｜RAZ 原文听写",
    description: "原文课程、本机账号、局域网学习。",
    type: "website",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
