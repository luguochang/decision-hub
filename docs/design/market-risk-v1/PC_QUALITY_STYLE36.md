# PC 质量卡样式整改36

35实际截图 `artifacts/mr-pc35/btc-default-download35_01.png` 中，研究质量使用浏览器默认定义列表缩进，状态占用过长竖向空间。检查源码后确认 `product-definition-list` 没有样式定义。

DEV仅改 ResearchQuality.tsx 新增专用class，以及 styles.css 的局部质量卡样式：与已有卡片一致的边框/间距/字体，标签和状态成对两列，长散列可换行。其他定义列表不受影响；报告内容和API保持原件。无需新逻辑测试；合并下一项必要核心修复后正式构建，并以同一真实BTC页面截图核对。Root35未改，未授样式已发布。