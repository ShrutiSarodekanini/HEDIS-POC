import { FileText, Upload, X } from 'lucide-react';

interface FileInputFieldProps {
  id: string;
  label: string;
  title: string;
  description: string;
  accept?: string;
  multiple?: boolean;
  required?: boolean;
  files: File[];
  browseLabel?: string;
  error?: string;
  onChange: (files: FileList | null) => void;
  onRemoveFile?: (index: number) => void;
}

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }

  if (size < 1024 * 1024) {
    return `${Math.ceil(size / 1024)} KB`;
  }

  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileInputField({
  id,
  label,
  title,
  description,
  accept,
  multiple,
  required,
  files,
  browseLabel = 'Browse files',
  error,
  onChange,
  onRemoveFile,
}: FileInputFieldProps) {
  return (
    <div className="form-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={id}
        type="file"
        className="sr-only-input"
        accept={accept}
        multiple={multiple}
        required={required}
        onChange={(event) => onChange(event.currentTarget.files)}
      />

      <label htmlFor={id} className="upload-dropzone">
        <div className="upload-icon-wrap" aria-hidden="true">
          <Upload size={18} />
        </div>
        <div className="upload-text-wrap">
          <p className="upload-title">{title}</p>
          <p className="upload-description">{description}</p>
          <span className="upload-button-text">{browseLabel}</span>
        </div>
      </label>

      {files.length > 0 ? (
        <ul className="selected-file-list" aria-label={`${label} selected files`}>
          {files.map((file, index) => (
            <li key={`${file.name}-${index}`} className="selected-file-row">
              <div className="selected-file-main">
                <FileText size={14} aria-hidden="true" />
                <span className="selected-file-name" title={file.name}>
                  {file.name}
                </span>
                <span className="selected-file-size">{formatBytes(file.size)}</span>
              </div>
              {onRemoveFile ? (
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => onRemoveFile(index)}
                  aria-label={`Remove ${file.name}`}
                >
                  <X size={14} />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {error ? <p className="field-error">{error}</p> : null}
    </div>
  );
}
