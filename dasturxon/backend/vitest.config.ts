import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    env: {
      NODE_ENV: 'test',
      DATABASE_URL: process.env.TEST_DATABASE_URL ?? 'postgres://dx:dx@localhost:5432/dasturxon_test',
      JOBS_ENABLED: 'false',
      DEV_OTP: 'true',
      DELIVERY_MOCK_SPEED: '1',
      UPLOAD_DIR: '/tmp/dasturxon-test-uploads',
    },
    fileParallelism: false,
    testTimeout: 30000,
    hookTimeout: 30000,
  },
});
