ARG NODE_BUILDER=registry.access.redhat.com/ubi9/nodejs-20
ARG NODE_RUNTIME=registry.access.redhat.com/ubi9/nodejs-20

FROM $NODE_BUILDER AS builder

# Set working directory
WORKDIR /app

# Copy package.json and package-lock.json before installing dependencies
# Ensure correct permissions by using --chown flag
COPY --chown=1001:0 transform-jsonata/package.json transform-jsonata/package-lock.json ./

# Install dependencies as non-root user
USER 1001
RUN npm install --frozen-lockfile

# Copy the rest of the application files
COPY --chown=1001:0 transform-jsonata/ .

# Use a minimal base image for runtime
FROM $NODE_RUNTIME

ARG VERSION=""

# Set working directory
WORKDIR /app

LABEL \
      com.redhat.component="openshift-serverless-1-kn-eventing-integrations-transform-jsonata-rhel9-container" \
      name="openshift-serverless-1/kn-eventing-integrations-transform-jsonata-rhel9" \
      version=$VERSION \
      release=$VERSION \
      summary="Red Hat OpenShift Serverless 1 Eventing Integrations Transform JSONata" \
      maintainer="serverless-support@redhat.com" \
      description="Red Hat OpenShift Serverless 1 Eventing Integrations Transform JSONata" \
      io.k8s.display-name="Red Hat OpenShift Serverless 1 Eventing Integrations Transform JSONata" \
      io.k8s.description="Red Hat OpenShift Serverless Eventing Integrations Transform JSONata" \
      io.openshift.tags=transform-jsonata \
      cpe="cpe:/a:redhat:openshift_serverless:1.37::el9"

# Copy built files and dependencies
COPY --from=builder --chown=1001:0 /app/package.json package.json
COPY --from=builder --chown=1001:0 /app/package-lock.json package-lock.json
COPY --from=builder --chown=1001:0 /app/jsonata.js jsonata.js
COPY --from=builder --chown=1001:0 /app/node_modules node_modules


COPY LICENSE /licenses/

# Switch to non-root user
USER 1001

ENV NODE_ENV=production

# Expose the application port
EXPOSE 8080

# Set the default command
CMD ["node", "jsonata.js"]
