#!/bin/sh
set -eu

batch_pattern=
for argument do
    case "$argument" in
        --batch=*) batch_pattern=${argument#--batch=} ;;
    esac
done

if [ -z "$batch_pattern" ]; then
    printf '%s\n' 'missing --batch output pattern' >&2
    exit 2
fi

page_path=$(printf "$batch_pattern" 1)
printf 'fake scan image\n' > "$page_path"
