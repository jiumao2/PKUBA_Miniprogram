import { Text, View } from "@tarojs/components";

export default function StandingsPage() {
  return <View className="page"><Text className="eyebrow">STANDINGS</Text><Text className="page-title">排名</Text><View className="state"><Text className="state-title">将在赛果模块后开放</Text><Text className="state-detail">排名只由服务端根据正式赛果计算，不使用客户端临时数据。</Text></View></View>;
}
