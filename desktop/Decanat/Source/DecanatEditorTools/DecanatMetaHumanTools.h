#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "DecanatMetaHumanTools.generated.h"

// UE 5.6 does not expose its character assembly subsystem to Python. Keep these
// editor-only calls separate from the shipped runtime and its local-only speech.
UCLASS()
class DECANATEDITORTOOLS_API UDecanatMetaHumanTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="Decanat|MetaHuman")
    static FString InspectCharacter(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|MetaHuman")
    static bool BeginFaceRig(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|MetaHuman")
    static bool BeginTextureDownload(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|MetaHuman")
    static bool BeginAssembly(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|MetaHuman")
    static bool ApplyPreviewAnimation(UObject* Actor, UObject* Animation, float PositionSeconds = 0.5f);

    UFUNCTION(BlueprintCallable, Category="Decanat|Authoring")
    static FString InspectPose(UObject* Actor);

    UFUNCTION(BlueprintCallable, Category="Decanat|Authoring")
    static FString ExportSkeleton(UObject* Mesh);

    UFUNCTION(BlueprintCallable, Category="Decanat|Authoring")
    static UObject* CreateAnimation(UObject* Mesh, const FString& JsonFile);

    UFUNCTION(BlueprintCallable, Category="Decanat|Authoring")
    static UObject* CreateOutfit(UObject* Mesh, const FString& JsonFile);

    UFUNCTION(BlueprintCallable, Category="Decanat|Authoring")
    static bool InstallOfficeOutfit(UObject* BlueprintAsset, UObject* Mesh);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool SendPIEMotion(const FString& Action);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static FString InspectPIEMotion();

    UFUNCTION(BlueprintCallable, Category="Decanat|FaceAuthoring")
    static FString InspectBlueprint(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|FaceAuthoring")
    static FString InspectPoseAsset(UObject* Asset);

    UFUNCTION(BlueprintCallable, Category="Decanat|FaceAuthoring")
    static UObject* CreateFaceAnimation(UObject* FaceMesh, UObject* MappingPose);

    UFUNCTION(BlueprintCallable, Category="Decanat|FaceAuthoring")
    static bool InstallFaceAnimation(UObject* CharacterBlueprint, UObject* FaceBlueprint);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static FString InspectPIEFace();

    UFUNCTION(BlueprintCallable, Category="Decanat|FaceAuthoring")
    static bool ConfigureFaceSource(UObject* FaceBlueprint);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool SendPIEFaceAudio(const FString& PayloadFile);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool StopPIEFaceAudio();

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool CapturePIEFace(bool bClosed);

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool BeginFaceGame();

    UFUNCTION(BlueprintCallable, Category="Decanat|Verification")
    static bool FinishPIEFaceAudio();
};
