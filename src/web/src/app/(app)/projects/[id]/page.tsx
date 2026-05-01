import { ProjectDetailView } from "@/components/projects/project-detail-view";

export default async function ProjectDetailPage(
  props: PageProps<"/projects/[id]">,
) {
  const { id } = await props.params;
  return <ProjectDetailView projectId={id} />;
}
