import { Button, Picker, Text, View } from "@tarojs/components";
import { useState } from "react";
import type { ScoresheetContextPlayerMapping, ScoresheetGameContextReview } from "@pkuba/scoresheet-domain";

export function GameContextReview({ review, readOnly, busy, onConfirm }: {
  review: ScoresheetGameContextReview;
  readOnly: boolean;
  busy: boolean;
  onConfirm: (mappings: ScoresheetContextPlayerMapping[]) => Promise<void>;
}) {
  const [mappings, setMappings] = useState<Record<string, string>>({});
  return <View className="mini-context-review">
    <Text className="mini-context-title">核对比赛信息变化</Text>
    <Text>原图和人工编辑均已保留。请对照原图核对，确认后重新校验，不会重新识别。</Text>
    {review.differences.map((difference) => <View className="mini-context-difference" key={difference.field}>
      <Text className="mini-context-label">{difference.label}</Text>
      <Text className="mini-context-before">原先：{difference.before}</Text>
      <Text>当前：{difference.after}</Text>
    </View>)}
    {review.player_conflicts.map((player) => {
      const key = `${player.side}:${player.row}`;
      const options = [{ id: "", name: "暂不确定，保留待核对" },
        ...player.choices.map((choice) => ({ id: choice.id, name: choice.label ?? choice.name }))];
      const selected = Math.max(0, options.findIndex((choice) => choice.id === mappings[key]));
      return <View className="mini-context-difference" key={key}>
        <Text className="mini-context-label">{player.side} 队第 {player.row} 行 · {player.name}</Text>
        <Text>球队或球员身份已改变，请明确选择数据归属。未选择仍不能发布。</Text>
        <Picker disabled={readOnly || busy} mode="selector" range={options} rangeKey="name" value={selected}
          onChange={(event) => setMappings((values) => ({ ...values, [key]: options[Number(event.detail.value)].id }))}>
          <View className="mini-context-picker">{options[selected].name} ▾</View>
        </Picker>
      </View>;
    })}
    {!readOnly && <Button disabled={busy} onClick={() => void onConfirm(
      review.player_conflicts.flatMap((player) => mappings[`${player.side}:${player.row}`]
        ? [{ side: player.side, row: player.row, player_id: mappings[`${player.side}:${player.row}`] }] : []),
    )}>{busy ? "正在复核…" : "保留编辑并确认复核"}</Button>}
  </View>;
}
