import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import dayjs from 'dayjs';
import 'dayjs/locale/ru';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles.css';

dayjs.locale('ru');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={ruRU}
      theme={{
        token: {
          colorPrimary: '#1FA37A',
          colorSuccess: '#1FA37A',
          colorError: '#E5484D',
          colorWarning: '#F5A524',
          colorInfo: '#3B82F6',
          borderRadius: 10,
          colorBgLayout: '#F4F6F8',
          fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
        },
        components: {
          Layout: { headerBg: '#ffffff', siderBg: '#ffffff', headerHeight: 60, headerPadding: '0 20px' },
          Menu: { itemSelectedBg: '#E8F6F0', itemSelectedColor: '#1FA37A', itemBorderRadius: 8, itemMarginInline: 10 },
          Card: { borderRadiusLG: 14 },
          Table: { headerBg: '#FAFBFC' },
        },
      }}
    >
      <AntApp>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
