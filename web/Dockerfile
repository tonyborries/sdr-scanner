FROM node

# TEMP DEV
RUN apt-get update
RUN apt-get install -y vim

RUN npm install -g vite

WORKDIR /app
COPY web_scanner/ /app
RUN npm install
RUN npm run build

EXPOSE 5173

CMD ["npm", "run" ,"dev"]
