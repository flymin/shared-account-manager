import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./style.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: "#177562",
          colorInfo: "#177562",
          borderRadius: 10,
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: { controlHeight: 38 },
          Table: {
            headerBg: "#f4f7f6",
            cellPaddingBlock: 8,
            cellPaddingInline: 12,
          },
          Card: { bodyPadding: 16, headerHeight: 44, headerFontSize: 14 },
          Modal: { titleFontSize: 19 },
        },
      }}
    >
      <AntApp message={{ maxCount: 2, duration: 2 }}>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
