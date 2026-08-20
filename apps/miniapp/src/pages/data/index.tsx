import { Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";

import { syncTabBar } from "../../tabbar";

export default function DataPage() {
  useDidShow(() => syncTabBar(3));
  return <View className="page"><Text className="page-title">数据</Text><View className="state"><Text className="state-title">暂无数据</Text></View></View>;
}
