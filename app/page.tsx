import type { Metadata } from "next";
import WordGame from "./WordGame";

export const metadata: Metadata = {
  title: "WordQuest Local｜RAZ 原文听写",
  description: "局域网内使用的 RAZ 原文听写、个人单词本与拼写远征学习站。",
};

export default function Home() { return <WordGame />; }
