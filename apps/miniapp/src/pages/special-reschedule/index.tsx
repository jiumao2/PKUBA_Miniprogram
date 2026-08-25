import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";

import "./index.css";

export default function SpecialReschedulePage() {
  const email = "pkubaoutward@163.com";
  return (
    <View className="page special-page">
      <Text className="page-title">特殊原因调赛</Text>
      <View className="rule-section">
        <Text className="rule-index">01</Text>
        <View>
          <Text className="rule-title">先参阅《参赛手册》并联系组委会</Text>
          <Text className="rule-body">办理前请先参阅《参赛手册》中关于特殊原因调赛与抽签的规定。申请条件、办理时限、邮件提交、所需说明或证明材料及后续步骤，均以《参赛手册》所述为准。请按手册要求发送邮件联系组委会，由组委会单独审议；小程序内不上传附件，也不自动判定理由是否成立。</Text>
        </View>
      </View>
      <View className="rule-section">
        <Text className="rule-index">02</Text>
        <View>
          <Text className="rule-title">协商新的比赛时间</Text>
          <Text className="rule-body">组委会同意后，双方领队协商目标时间。容量、场地与其他比赛仍需由管理员核对。</Text>
        </View>
      </View>
      <View className="rule-section">
        <Text className="rule-index">03</Text>
        <View>
          <Text className="rule-title">协商不成时申请抽签</Text>
          <Text className="rule-body">双方无法协商一致时，请继续按照《参赛手册》规定的流程申请抽签，并配合组委会完成后续程序。抽签由组委会主持，最终结果由超级管理员录入系统；小程序仅作流程提示，不在系统内执行抽签。</Text>
        </View>
      </View>
      <View className="contact-line">
        <View><Text className="contact-label">组委会公邮</Text><Text className="contact-value">{email}</Text></View>
        <Button className="contact-copy" onClick={() => void Taro.setClipboardData({ data: email })}>复制邮箱</Button>
      </View>
    </View>
  );
}
