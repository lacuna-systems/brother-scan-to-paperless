{{/*
Expand the name of the chart.
*/}}
{{- define "brother-scan-to-paperless.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name, truncated to the 63 characters
allowed in DNS names.
*/}}
{{- define "brother-scan-to-paperless.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "brother-scan-to-paperless.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "brother-scan-to-paperless.labels" -}}
helm.sh/chart: {{ include "brother-scan-to-paperless.chart" . }}
{{ include "brother-scan-to-paperless.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "brother-scan-to-paperless.selectorLabels" -}}
app.kubernetes.io/name: {{ include "brother-scan-to-paperless.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image reference. The tag defaults to "<backend>-<appVersion>" because the
brscan4 and brscan5 images are published under backend-prefixed tags.
*/}}
{{- define "brother-scan-to-paperless.image" -}}
{{- $backend := .Values.image.backend }}
{{- if not (has $backend (list "brscan4" "brscan5")) }}
{{- fail "image.backend must be brscan4 or brscan5" }}
{{- end }}
{{- $tag := .Values.image.tag | default (printf "%s-%s" $backend .Chart.AppVersion) }}
{{- if .Values.image.digest }}
{{- printf "%s:%s@%s" .Values.image.repository $tag .Values.image.digest }}
{{- else }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}
{{- end }}

{{- define "brother-scan-to-paperless.apiMode" -}}
{{- $mode := .Values.deliveryMode }}
{{- if not (has $mode (list "api" "consume")) }}
{{- fail "deliveryMode must be api or consume" }}
{{- end }}
{{- if eq $mode "api" }}true{{ end }}
{{- end }}

{{- define "brother-scan-to-paperless.hostIP" -}}
{{- .Values.scanner.hostIP | default .Values.service.loadBalancerIP | required "scanner.hostIP or service.loadBalancerIP is required: the scanner sends Scan-button events to this address" }}
{{- end }}

{{- define "brother-scan-to-paperless.tokenSecretName" -}}
{{- if .Values.paperless.existingSecret }}
{{- .Values.paperless.existingSecret }}
{{- else }}
{{- printf "%s-paperless" (include "brother-scan-to-paperless.fullname" .) }}
{{- end }}
{{- end }}

{{- define "brother-scan-to-paperless.tokenSecretKey" -}}
{{- if .Values.paperless.existingSecret }}
{{- .Values.paperless.existingSecretKey }}
{{- else -}}
apiToken
{{- end }}
{{- end }}

{{- define "brother-scan-to-paperless.stagingClaimName" -}}
{{- .Values.persistence.existingClaim | default (printf "%s-staging" (include "brother-scan-to-paperless.fullname" .)) }}
{{- end }}

{{/*
Comma-separated Paperless tag IDs, validated as positive integers.
*/}}
{{- define "brother-scan-to-paperless.paperlessTags" -}}
{{- $tags := list }}
{{- range .Values.paperless.tags }}
{{- $tag := toString . }}
{{- /* YAML numbers arrive as float64, which toString renders as 1.2e+06. */}}
{{- if and (kindIs "float64" .) (eq (int64 . | float64) .) }}
{{- $tag = int64 . | toString }}
{{- end }}
{{- if not (regexMatch "^[1-9][0-9]*$" $tag) }}
{{- fail (printf "paperless.tags must contain positive tag IDs, got %q; the Paperless upload API does not accept tag names" $tag) }}
{{- end }}
{{- $tags = append $tags $tag }}
{{- end }}
{{- join "," $tags }}
{{- end }}
