#include "DecanatMetaHumanTools.h"
#include "Editor.h"
#include "MetaHumanCharacter.h"
#include "MetaHumanCollectionPipeline.h"
#include "MetaHumanCharacterEditorSubsystem.h"
#include "Subsystem/MetaHumanCharacterBuild.h"
#include "Cloud/MetaHumanARServiceRequest.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Modules/ModuleManager.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/Actor.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, DecanatEditorTools)

namespace
{
    UMetaHumanCharacterEditorSubsystem* GetCharacterEditor()
    {
        return GEditor ? GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>() : nullptr;
    }

    bool Prepare(UMetaHumanCharacter* Character, UMetaHumanCharacterEditorSubsystem* Editor)
    {
        if (!Character || !Editor || IsRunningCommandlet()) return false;
        return Editor->IsObjectAddedForEditing(Character) || Editor->TryAddObjectToEdit(Character);
    }
}

FString UDecanatMetaHumanTools::InspectCharacter(UObject* Asset)
{
    TSharedRef<FJsonObject> Status = MakeShared<FJsonObject>();
    auto* Character = Cast<UMetaHumanCharacter>(Asset);
    auto* Editor = GetCharacterEditor();
    Status->SetBoolField(TEXT("valid_character"), Character && Character->IsCharacterValid());
    Status->SetBoolField(TEXT("face_rig"), Character && Character->HasFaceDNA());
    Status->SetBoolField(TEXT("texture_sources"), Character && Character->HasHighResolutionTextures());
    Status->SetBoolField(TEXT("rig_pending"), Character && Editor && Editor->IsAutoRiggingFace(Character));
    Status->SetBoolField(TEXT("textures_pending"), Character && Editor && Editor->IsRequestingHighResolutionTextures(Character));
    FText Issue;
    const bool bCanAssemble = Character && Editor && Editor->CanBuildMetaHuman(Character, Issue);
    Status->SetBoolField(TEXT("can_assemble"), bCanAssemble);
    Status->SetStringField(TEXT("assembly_issue"), Issue.ToString());
    FString Result;
    FJsonSerializer::Serialize(Status, TJsonWriterFactory<>::Create(&Result));
    return Result;
}

bool UDecanatMetaHumanTools::BeginFaceRig(UObject* Asset)
{
    auto* Character = Cast<UMetaHumanCharacter>(Asset);
    auto* Editor = GetCharacterEditor();
    if (!Prepare(Character, Editor)) return false;
    if (Character->HasFaceDNA()) return true;
    if (!Editor->IsAutoRiggingFace(Character)) {
        Editor->AutoRigFace(Character, UE::MetaHuman::ERigType::JointsAndBlendshapes);
    }
    return Character->HasFaceDNA() || Editor->IsAutoRiggingFace(Character);
}

bool UDecanatMetaHumanTools::BeginTextureDownload(UObject* Asset)
{
    auto* Character = Cast<UMetaHumanCharacter>(Asset);
    auto* Editor = GetCharacterEditor();
    if (!Prepare(Character, Editor)) return false;
    if (Character->HasHighResolutionTextures()) return true;
    if (!Editor->IsRequestingHighResolutionTextures(Character)) {
        Editor->RequestHighResolutionTextures(Character, ERequestTextureResolution::Res2k);
    }
    return Character->HasHighResolutionTextures() || Editor->IsRequestingHighResolutionTextures(Character);
}

bool UDecanatMetaHumanTools::BeginAssembly(UObject* Asset)
{
    auto* Character = Cast<UMetaHumanCharacter>(Asset);
    auto* Editor = GetCharacterEditor();
    if (!Prepare(Character, Editor)) return false;
    FText Issue;
    if (!Editor->CanBuildMetaHuman(Character, Issue)) {
        UE_LOG(LogTemp, Error, TEXT("Decanat MetaHuman assembly: %s"), *Issue.ToString());
        return false;
    }
    // Assembly can replace existing generated assets. Only start with a new folder;
    // callers must inspect the real output before setting AvatarClass/readiness.
    if (IFileManager::Get().DirectoryExists(*(FPaths::ProjectContentDir() / TEXT("MetaHumans/Assembled/Decanat")))) {
        UE_LOG(LogTemp, Error, TEXT("Decanat MetaHuman output exists; inspect existing assets before rebuilding."));
        return false;
    }
    FMetaHumanCharacterEditorBuildParameters Parameters;
    Parameters.AbsoluteBuildPath = TEXT("/Game/MetaHumans/Assembled");
    Parameters.NameOverride = TEXT("Decanat");
    // Use the standard UE Optimized/High export: a standalone runtime actor
    // with the Body/Face components expected by the desktop and ACE integration.
    // UE 5.6.1 also dereferences this nominally optional override on completion.
    UClass* PipelineClass = LoadClass<UMetaHumanCollectionPipeline>(nullptr,
        TEXT("/MetaHumanCharacter/BuildPipeline/BP_DefaultLegacyPipeline_High.BP_DefaultLegacyPipeline_High_C"));
    if (!PipelineClass) {
        UE_LOG(LogTemp, Error, TEXT("Decanat MetaHuman UE Optimized/High pipeline is unavailable."));
        return false;
    }
    Parameters.PipelineOverride = NewObject<UMetaHumanCollectionPipeline>(GetTransientPackage(), PipelineClass);
    FMetaHumanCharacterEditorBuild::BuildMetaHumanCharacter(Character, Parameters);
    return true;
}

bool UDecanatMetaHumanTools::ApplyPreviewAnimation(UObject* Actor, UObject* Animation, float PositionSeconds)
{
    auto* Preview = Cast<AActor>(Actor);
    auto* Sequence = Cast<UAnimSequence>(Animation);
    if (!Preview || !Sequence || !Sequence->GetSkeleton()) return false;
    TArray<USkeletalMeshComponent*> Meshes;
    Preview->GetComponents(Meshes);
    auto* FoundBody = Meshes.FindByPredicate([](const USkeletalMeshComponent* Mesh) {
        return Mesh->GetFName() == FName(TEXT("Body"));
    });
    if (!FoundBody || !(*FoundBody)->GetSkeletalMeshAsset() ||
        !Sequence->GetSkeleton()->IsCompatibleMesh((*FoundBody)->GetSkeletalMeshAsset())) return false;
    auto* Body = *FoundBody;
    Body->PlayAnimation(Sequence, false);
    Body->SetPosition(FMath::Clamp(PositionSeconds, 0.0f, Sequence->GetPlayLength()), false);
    // Editor screenshots need an evaluated pose even when realtime viewport
    // animation is disabled. This affects the unsaved preview actor only.
    Body->TickAnimation(0.0f, false);
    Body->RefreshBoneTransforms();
    for (auto* Mesh : Meshes) {
        if (Mesh == Body) continue;
        Mesh->TickAnimation(0.0f, false);
        Mesh->RefreshBoneTransforms();
    }
    return true;
}
