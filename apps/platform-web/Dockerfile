FROM nginx:1.27-alpine

COPY --chmod=0644 index.html /usr/share/nginx/html/index.html
COPY --chmod=0644 styles.css /usr/share/nginx/html/styles.css
COPY --chmod=0644 brand-official.css /usr/share/nginx/html/brand-official.css
COPY --chmod=0644 app.js /usr/share/nginx/html/app.js
COPY --chmod=0644 nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
