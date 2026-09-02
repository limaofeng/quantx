import { SelectionModelLibrary } from '../components';
import { ResearchCenterFrame } from '../components/ResearchCenterFrame';

export default function ResearchModelsPage() {
  return (
    <ResearchCenterFrame
      title="模型库"
      description="管理具备 FINAL 证据的模型版本，并执行受控的人工阶段流转。"
    >
      <SelectionModelLibrary />
    </ResearchCenterFrame>
  );
}
