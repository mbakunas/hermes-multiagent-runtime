FROM mcr.microsoft.com/devcontainers/python:3.13

RUN apt-get update && apt-get install -y \
    git \
    curl \
    nodejs \
    npm

RUN npm install -g @anthropic-ai/claude-code

RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg

RUN chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg

RUN echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
> /etc/apt/sources.list.d/github-cli.list

RUN apt-get update && apt-get install -y gh