module.exports = {
  ci: {
    collect: {
      url: ['http://localhost:3000'],
      startServerCommand: 'npm run preview',
      startServerReadyPattern: 'Local:',
      startServerReadyTimeout: 30000,
    },
    assert: {
      assertions: {
        'categories:performance': ['warn', { minScore: 0.8 }],
        'categories:accessibility': ['error', { minScore: 0.9 }],
        'categories:best-practices': ['warn', { minScore: 0.8 }],
        'categories:seo': ['warn', { minScore: 0.8 }],

        // Core Web Vitals
        'first-contentful-paint': ['warn', { maxNumericValue: 2000 }],
        'largest-contentful-paint': ['warn', { maxNumericValue: 2500 }],
        'first-meaningful-paint': ['warn', { maxNumericValue: 2000 }],
        'speed-index': ['warn', { maxNumericValue: 3000 }],
        'cumulative-layout-shift': ['warn', { maxNumericValue: 0.1 }],

        // Bundle size
        'total-byte-weight': ['warn', { maxNumericValue: 2000000 }], // 2MB
        'unused-javascript': ['warn', { maxNumericValue: 500000 }], // 500KB
        'unused-css-rules': ['warn', { maxNumericValue: 100000 }], // 100KB

        // Images
        'modern-image-formats': 'warn',
        'offscreen-images': 'warn',
        'efficient-animated-content': 'warn',

        // Performance
        'uses-text-compression': 'warn',
        'render-blocking-resources': 'warn',
        'unminified-css': 'error',
        'unminified-javascript': 'error',
      },
    },
    upload: {
      target: 'temporary-public-storage',
    },
  },
};
